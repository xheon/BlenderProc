import bpy
import bmesh
from mathutils import Vector

import numpy as np


def triangulate(obj, transform=True, triangulate=True, apply_modifiers=False):
    """
    :obj: object to triangulate, must be a mesh
    :transform: transform to world coordinates if True
    :triangulate: perform triangulation if True
    :apply_modifiers: applies modifiers if any and True
    Returns a transformed, triangulated copy of the mesh (much smaller in size and can be used for quicker maths)
    """
    assert(obj.type == 'MESH')

    if apply_modifiers and obj.modifiers:
        me = obj.to_mesh(bpy.context.scene, True, 'PREVIEW', calc_tessface=False)
        bm = bmesh.new()
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    else:
        me = obj.data
        if obj.mode == 'EDIT':
            bm_orig = bmesh.from_edit_mesh(me)
            bm = bm_orig.copy()
        else:
            bm = bmesh.new()
            bm.from_mesh(me)

    # Remove custom data layers to save memory
    for elem in (bm.faces, bm.edges, bm.verts, bm.loops):
        for layers_name in dir(elem.layers):
            if not layers_name.startswith("_"):
                layers = getattr(elem.layers, layers_name)
                for layer_name, layer in layers.items():
                    layers.remove(layer)

    if transform:
        bm.transform(obj.matrix_world)

    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces)

    return bm

def local_to_world(cords, world):
    """
    :param cords: coordinates a tuple of 3 values for x,y,z
    :param world: world matrix <- transformation matrix
    Returns a cords transformed to the given transformation world matrix
    """
    return [world @ Vector(cord) for cord in cords]


def world_to_camera(cam, coord, world2camera):
    co_local = world2camera @ coord
    z = -co_local.z

    frame = [-v for v in cam.view_frame(scene=bpy.context.scene)[:3]]
    if cam.type != 'ORTHO':
        if z == 0.0:
            return Vector((0.5, 0.5, 0.0))
        else:
            frame = [(v / (v.z / z)) for v in frame]

    min_x, max_x = frame[1].x, frame[2].x
    min_y, max_y = frame[0].y, frame[1].y

    x = (co_local.x - min_x) / (max_x - min_x)
    y = (co_local.y - min_y) / (max_y - min_y)

    return Vector((x, y, z))


def get_bounds(obj):
    """
    :param obj: a mesh object
    :returns [8x[3xfloat]] the object aligned bounding box coordinates in world coordinates
    """
    return local_to_world(obj.bound_box, obj.matrix_world)

def check_bb_intersection(obj1,obj2):
    """
    Checks if there is a bounding box collision, these don't have to be axis-aligned, but if they are not:
        The surrounding/including axis-aligned bounding box is calculated and used to check the intersection

    :param obj1: object 1  to check for intersection, must be a mesh
    :param obj2: object 2  to check for intersection, must be a mesh
    returns a boolean
    """
    b1w = get_bounds(obj1)
    def min_and_max_point(bb):
        """
        Find the minimum and maximum point of the bounding box
        :param bb: bounding box
        :return: min, max
        """
        values = np.array(bb)
        return np.min(values, axis=0), np.max(values, axis=0)
    # get min and max point of the axis-aligned bounding box
    min_b1, max_b1 = min_and_max_point(b1w)
    b2w = get_bounds(obj2)
    # get min and max point of the axis-aligned bounding box
    min_b2, max_b2 = min_and_max_point(b2w)
    collide = True
    for min_b1_val, max_b1_val, min_b2_val, max_b2_val in zip(min_b1, max_b1, min_b2, max_b2):
        # inspired by this:
        # https://stackoverflow.com/questions/20925818/algorithm-to-check-if-two-boxes-overlap
        # Checks in each dimension, if there is an overlap if this happens it must be an overlap in 3D, too.
        def is_overlapping_1D(x_min_1, x_max_1, x_min_2, x_max_2):
            # returns true if the min and max values are overlapping
            return x_max_1 >= x_min_2 and x_max_2 >= x_min_1
        collide = collide and is_overlapping_1D(min_b1_val, max_b1_val, min_b2_val, max_b2_val)
    return collide


def check_intersection(obj, obj2, cache = None):
    """
    Checks if the two objects are colliding, the code is from:
        https://blender.stackexchange.com/questions/9073/how-to-check-if-two-meshes-intersect-in-python

    The check is performed along the edges from the object, which has less edges.

    :param obj1: object 1 to check for intersection, must be a mesh
    :param obj2: object 2 to check for intersection, must be a mesh
    returns a boolean and the cache of the objects, which already have been triangulated
    """
    assert(obj != obj2)

    if cache is None:
        cache = {}
    
    assert(type(cache) == type({})) # cache must be a dict

    # Triangulate (Load from cache if available)
    if obj.name in cache:
        bm = cache[obj.name]
    else:
        bm = triangulate(obj, transform=True, triangulate=True)
        cache[obj.name] = bm

    if obj2.name in cache:
        bm2 = cache[obj2.name]
    else:
        bm2 = triangulate(obj2, transform=True, triangulate=True)
        cache[obj2.name] = bm2

    # If bm has more edges, use bm2 instead for looping over its edges
    # (so we cast less rays from the simpler object to the more complex object)
    if len(bm.edges) > len(bm2.edges):
        bm2, bm = bm, bm2

    # Create a real mesh 
    scene = bpy.context.scene
    me_tmp = bpy.data.meshes.new(name="~temp~")
    bm2.to_mesh(me_tmp)
    bm2.free()
    obj_tmp = bpy.data.objects.new(name=me_tmp.name, object_data=me_tmp)
    scene.collection.objects.link(obj_tmp)
    bpy.context.view_layer.update()
    
    # this ray_cast is performed in object coordinates, but both objects were moved in world coordinates
    # so the world_matrix is the identity matrix
    ray_cast = obj_tmp.ray_cast

    intersect = False

    EPS_NORMAL = 0.000001
    EPS_CENTER = 0.01  # should always be bigger

    #for ed in me_tmp.edges:
    for ed in bm.edges:
        v1, v2 = ed.verts

        # setup the edge with an offset
        co_1 = v1.co.copy()
        co_2 = v2.co.copy()
        co_mid = (co_1 + co_2) * 0.5
        no_mid = (v1.normal + v2.normal).normalized() * EPS_NORMAL
        # interpolation between co_1 and co_mid, with a small value to get away from the original co_1
        # plus the average direction of the normal to get away from the object itself
        co_1 = co_1.lerp(co_mid, EPS_CENTER) + no_mid
        co_2 = co_2.lerp(co_mid, EPS_CENTER) + no_mid

        t, co, no, index = ray_cast(co_1, (co_2 - co_1).normalized(), distance=ed.calc_length())
        if index != -1:
            intersect = True
            break

    scene.collection.objects.unlink(obj_tmp)
    bpy.data.objects.remove(obj_tmp)
    bpy.data.meshes.remove(me_tmp)

    # new method to udpate scene
    bpy.context.view_layer.update()

    return intersect, cache

def check_if_uv_coordinates_are_set(obj: bpy.types.Object):
    """
    :param obj: should be an object, which has a mesh
    """
    if len(obj.data.uv_layers) > 1:
        raise Exception("This only support objects which only have one uv layer.")
    for layer in obj.data.uv_layers:
        max_val = np.max([list(uv_coords.uv) for uv_coords in layer.data])
        return max_val > 1e-7
    return False

def vector_to_euler(vector, vector_type):
    """
    :param vector: UP (for MESH objs) of FORWARD (for LIGHT/CAMERA objs) vector. Type: mathutils Vector.
    :param vector_type: Type of an input vector: UP or FORWARD. Type: string.
    :return: Corresponding Euler angles XYZ-triplet. Type: mathutils Euler.
    """
    # Check vector type
    if vector_type == "UP":
        # UP vectors are used for MESH type objects
        euler_angles = vector.to_track_quat('Z', 'Y').to_euler()
    elif vector_type == "FORWARD":
        # FORWARD vectors are used for LIGHT and CAMERA type objects
        euler_angles = vector.to_track_quat('-Z', 'Y').to_euler()
    else:
        raise Exception("Unknown vector type: " + vector_type)

    return euler_angles

def add_object_only_with_vertices(vertices, name='NewVertexObject'):
    """
    Generates a new object with the given vertices, no edges or faces are generated.

    :param vertices: [[float, float, float]] list of vertices
    :param name: str name of the new object
    :return the generated obj
    """
    mesh = bpy.data.meshes.new('mesh')
    # create new object
    obj = bpy.data.objects.new(name, mesh)
    # TODO check if this always works?
    col = bpy.data.collections.get('Collection')
    # link object in collection
    col.objects.link(obj)

    # convert vertices to mesh
    bm = bmesh.new()
    for v in vertices:
        bm.verts.new(v)
    bm.to_mesh(mesh)
    bm.free()
    return obj

def add_object_only_with_direction_vectors(vertices, normals, radius=1.0, name='NewDirectionObject'):
    """
    Generates a new object with the given vertices, no edges or faces are generated.

    :param vertices: [[float, float, float]] list of vertices
    :param name: str name of the new object
    :return the generated obj
    """
    if len(vertices) != len(normals):
        raise Exception("The lenght of the vertices and normals is not equal!")

    mesh = bpy.data.meshes.new('mesh')
    # create new object
    obj = bpy.data.objects.new(name, mesh)
    # TODO check if this always works?
    col = bpy.data.collections.get('Collection')
    # link object in collection
    col.objects.link(obj)

    # convert vertices to mesh
    bm = bmesh.new()
    for v, n in zip(vertices, normals):
        v1 = bm.verts.new(v)
        new_vert = v + n * radius
        v2 = bm.verts.new(new_vert)
        bm.edges.new([v1, v2])
    bm.to_mesh(mesh)
    bm.free()
    return obj


def add_cube_based_on_bb(bouding_box, name='NewCube'):
    """
    Generates a cube based on the given bounding box, the bounding_box can be generated with our get_bounds(obj) fct.

    :param bounding_box: bound_box [8x[3xfloat]], with 8 vertices for each corner
    :param name: name of the new cube
    :return the generated object
    """
    if len(bouding_box) != 8:
        raise Exception("The amount of vertices is wrong for this bounding box!")
    mesh = bpy.data.meshes.new('mesh')
    # create new object
    obj = bpy.data.objects.new(name, mesh)
    # TODO check if this always works?
    col = bpy.data.collections.get('Collection')
    # link object in collection
    col.objects.link(obj)

    # convert vertices to mesh
    new_vertices = []
    bm = bmesh.new()
    for v in bouding_box:
        new_vertices.append(bm.verts.new(v))
    # create all 6 surfaces, the ordering is depending on the ordering of the vertices in the bounding box
    bm.faces.new([new_vertices[0], new_vertices[1], new_vertices[2], new_vertices[3]])
    bm.faces.new([new_vertices[0], new_vertices[4], new_vertices[5], new_vertices[1]])
    bm.faces.new([new_vertices[1], new_vertices[5], new_vertices[6], new_vertices[2]])
    bm.faces.new([new_vertices[2], new_vertices[3], new_vertices[7], new_vertices[6]])
    bm.faces.new([new_vertices[0], new_vertices[4], new_vertices[7], new_vertices[3]])
    bm.faces.new([new_vertices[4], new_vertices[5], new_vertices[6], new_vertices[7]])
    bm.to_mesh(mesh)
    bm.free()
    return obj

def get_all_mesh_objects():
    """
    Returns a list of all mesh objects in the scene
    :return: a list of all mesh objects
    """
    return [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

def get_all_materials():
    """
    Returns a list of all materials used and unused
    :return: a list of all materials
    """
    return list(bpy.data.materials)

def get_all_textures():
    """
    Returns a list of all textures.
    :return: All textures. Type: list.
    """
    return list(bpy.data.textures)

def load_image(file_path, num_channels=3):
    """ Load the image at the given path returns its pixels as a numpy array.

    The alpha channel is neglected.

    :param file_path: The path to the image.
    :param num_channels: Number of channels to return.
    :return: The numpy array
    """
    # load image with blender function
    img = bpy.data.images.load(file_path, check_existing=False)
    # convert image to proper size
    size = img.size
    channels = img.channels
    img = np.array(img.pixels).reshape(size[1], size[0], channels)
    img = np.flip(img, axis=0)
    if file_path.endswith('.png') or file_path.endswith('.jpg'):
        # convert the 0 to 1 space to 0 ... 255 and save it as uint8
        img = (img * 255).astype(np.uint8)
    loaded.buffers_free()
    return img[:, :, :num_channels]

def get_bound_volume(obj):
    """ Gets the volume of a possible orientated bounding box.
    :param obj: Mesh object.
    :return: volume of a bounding box.
    """
    bb = get_bounds(obj)
    # Search for the point which is the maximum distance away from the first point
    # we call this first point min and the furthest away point max
    # the vector between the two is a diagonal of the bounding box
    min_point, max_point = bb[0], None
    max_dist = -1
    for point in bb:
        dist = (point - min_point).length
        if dist > max_dist:
            max_point = point
            max_dist = dist
    diag = max_point - min_point
    # use the diagonal to calculate the volume of the box
    return abs(diag[0]) * abs(diag[1]) * abs(diag[2])

def duplicate_objects(objects):
    """
    Creates duplicates of objects, first duplicates are given name <orignial_object_name>.001
    
    :param objects: an object or a list of objects to be duplicated
    :return: a list of objects
    """
    if not isinstance(objects, list):
        objects = [objects]

    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.ops.object.duplicate()
    duplicates = bpy.context.selected_objects
    bpy.ops.object.select_all(action='DESELECT')
    return duplicates


def hide_all_geometry():
    # Hide all objects
    for collection in bpy.data.collections:
        # two step approach to hide objects to avoid crash on Ubuntu: https://blender.stackexchange.com/questions/169672/object-hide-render-crashes-blender-before-starting-to-bake/170197#170197
        objects = [obj for obj in collection.all_objects]
        for obj in objects:
            # print(f"Hide {obj.name}")
            obj.hide_viewport = True
            obj.hide_render = True


def show_collection(collection):
    for obj in collection.children:
        obj.hide_viewport = False
        obj.hide_render = False


def get_centroid(mesh):
    x, y, z = [sum([v.co[i] for v in mesh.data.vertices]) for i in range(3)]
    count = float(len(mesh.data.vertices))
    center = mesh.matrix_world @ (Vector((x, y, z)) / count)

    return center


def write_ply(vertices, indices, output_file):
    if indices is None:
        indices = []

    file = open(output_file, 'w')
    file.write('ply \n')
    file.write('format ascii 1.0\n')
    file.write('element vertex {:d}\n'.format(len(vertices)))
    file.write('property float x\n')
    file.write('property float y\n')
    file.write('property float z\n')
    file.write('element face {:d}\n'.format(len(indices)))
    file.write('property list uchar uint vertex_indices\n')
    file.write('end_header\n')

    for vertex in vertices:
        file.write("{:f} {:f} {:f}\n".format(vertex[0], vertex[1], vertex[2]))
    for ind in indices:
        file.write('3 {:d} {:d} {:d}\n'.format(ind[0], ind[1], ind[2], ))
    file.close()


# color palette for nyu40 labels
def create_color_palette():
    return [
       (0, 0, 0),
       (174, 199, 232),		# wall
       (152, 223, 138),		# floor
       (31, 119, 180), 		# cabinet
       (255, 187, 120),		# bed
       (188, 189, 34), 		# chair
       (140, 86, 75),  		# sofa
       (255, 152, 150),		# table
       (214, 39, 40),  		# door
       (197, 176, 213),		# window
       (148, 103, 189),		# bookshelf
       (196, 156, 148),		# picture
       (23, 190, 207), 		# counter
       (178, 76, 76),
       (247, 182, 210),		# desk
       (66, 188, 102),
       (219, 219, 141),		# curtain
       (140, 57, 197),
       (202, 185, 52),
       (51, 176, 203),
       (200, 54, 131),
       (92, 193, 61),
       (78, 71, 183),
       (172, 114, 82),
       (255, 127, 14), 		# refrigerator
       (91, 163, 138),
       (153, 98, 156),
       (140, 153, 101),
       (158, 218, 229),		# shower curtain
       (100, 125, 154),
       (178, 127, 135),
       (120, 185, 128),
       (146, 111, 194),
       (44, 160, 44),  		# toilet
       (112, 128, 144),		# sink
       (96, 207, 209),
       (227, 119, 194),		# bathtub
       (213, 92, 176),
       (94, 106, 211),
       (82, 84, 163),  		# otherfurn
       (100, 85, 144),
    ]
