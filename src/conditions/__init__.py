# coding: utf-8
import itertools
import math
import random
import inspect
from collections import namedtuple, defaultdict
from decimal import Decimal
from enum import Enum

from typing import (
    Callable, Any, Iterable,
    Dict, List, Tuple, NamedTuple, Set,
)

import srctools
import utils
import vbsp_options
import comp_consts as consts
from instanceLocs import resolve as resolve_inst
from srctools import (
    Property,
    Vec_tuple, Vec,
    Entity, Output, Solid, Side, UVAxis
)


LOGGER = utils.getLogger(__name__, alias='cond.core')

# Stuff we get from VBSP in init()
GLOBAL_INSTANCES = set()
ALL_INST = set()
VMF = None  # type: srctools.VMF

conditions = []
FLAG_LOOKUP = {}
RESULT_LOOKUP = {}
RESULT_SETUP = {}

# Used to dump a list of the flags, results, meta-conditions
ALL_FLAGS = []
ALL_RESULTS = []
ALL_META = []

GOO_LOCS = {}  # A mapping from blocks containing goo to the top face
GOO_FACE_LOC = {}  # A mapping from face origin -> face for top faces.

# A VMF containing template brushes, which will be loaded in and retextured
# The first list is for world brushes, the second are func_detail brushes. The third holds overlays.
TEMPLATES = {}  # type: Dict[str, Dict[str, Tuple[List[Solid], List[Solid], List[Entity]]]]
TEMPLATE_LOCATION = 'bee2/templates.vmf'

# A template shaped like embeddedVoxel blocks
TEMP_EMBEDDED_VOXEL = 'BEE2_EMBEDDED_VOXEL'


class SWITCH_TYPE(Enum):
    """The methods useable for switch options."""
    FIRST = 'first'  # choose the first match
    LAST = 'last'  # choose the last match
    RANDOM = 'random'  # Randomly choose
    ALL = 'all'  # Run all matching commands


class TEMP_TYPES(Enum):
    """Value used for import_template()'s force_type parameter.
    """
    default = 0  # Based on the original VMF settings
    world = 1  # Import and add to world
    detail = 2  # Import as a func_detail

Template = namedtuple('Template', ['world', 'detail', 'overlay', 'orig_ids'])


class MAT_TYPES(Enum):
    """The values saved in the solidGroup.color attribute."""
    black = 0
    white = 1

    def __str__(self):
        if self is MAT_TYPES.black:
            return 'black'
        if self is MAT_TYPES.white:
            return 'white'

# A dictionary mapping origins to their brushes
solidGroup = NamedTuple('solidGroup', [
    ('face', Side),
    ('solid', Solid),
    ('normal', Vec), # The normal of the face.
    ('color', MAT_TYPES),
])
SOLIDS = {}  # type: Dict[Vec_tuple, solidGroup]

# The input/output connection values defined for each item.
# Each is a tuple of (inst_name, command) values, ready to be passed to
# VLib.Output().
# If the command is '', no in/output is present.
ItemConnections = namedtuple('ItemConnections', [
    'in_act', 'in_deact', 'out_act', 'out_deact',
])
CONNECTIONS = {}

# For each class, a list of item IDs of that type.
ITEM_CLASSES = defaultdict(list)

# The special tbeam polarity input from ITEM_TBEAM. Works like above.
TBEAM_CONN_ACT = TBEAM_CONN_DEACT = (None, '')


xp = Vec_tuple(1, 0, 0)
xn = Vec_tuple(-1, 0, 0)
yp = Vec_tuple(0, 1, 0)
yn = Vec_tuple(0, -1, 0)
zp = Vec_tuple(0, 0, 1)
zn = Vec_tuple(0, 0, -1)

DIRECTIONS = {
    # Translate these words into a normal vector
    '+x': xp,
    '-x': xn,

    '+y': yp,
    '-y': yn,

    '+z': zp,
    '-z': zn,

    'x': xp,  # For with allow_inverse
    'y': yp,
    'z': zp,

    'up': zp,
    'dn': zn,
    'down': zn,
    'floor': zp,
    'ceiling': zn,
    'ceil': zn,

    'n': yp,
    'north': yp,
    's': yn,
    'south': yn,

    'e': xp,
    'east': xp,
    'w': xn,
    'west': xn,

    'wall': 'WALL',  # Special case, not floor/ceiling
    'walls': 'WALL',
}

INST_ANGLE = {
    # IE up = zp = floor
    zp: "0 0 0",
    zn: "0 0 0",

    xn: "0 0 0",
    yn: "0 90 0",
    xp: "0 180 0",
    yp: "0 270 0",

}

PETI_INST_ANGLE = {
    # The angles needed to point a PeTI instance in this direction
    # IE north = yn

    yn: "0 0 90",
    xp: "0 90 90",
    yp: "0 180 90",
    xn: "0 270 90",
}

del xp, xn, yp, yn, zp, zn

B = MAT_TYPES.black
W = MAT_TYPES.white
TEMPLATE_RETEXTURE = {
    # textures map -> surface types for template brushes.
    # It's mainly for grid size and colour - floor/ceiling textures
    # will be used instead at those orientations

    'metal/black_wall_metal_002c': (B, 'wall'),
    'metal/black_wall_metal_002a': (B, '2x2'),
    'metal/black_wall_metal_002b': (B, '4x4'),

    'tile/white_wall_tile001a': (W, 'wall'),
    'tile/white_wall_tile003a': (W, 'wall'),
    'tile/white_wall_tile003b': (W, 'wall'),
    'tile/white_wall_tile003c': (W, '2x2'),
    'tile/white_wall_tile003h': (W, 'wall'),
    'tile/white_wall_state': (W, '2x2'),
    'tile/white_wall_tile003f': (W, '4x4'),

    # No black portal-placement texture, so use the bullseye instead
    'metal/black_floor_metal_bullseye_001': (B, 'special'),
    'tile/white_wall_tile004j': (W, 'special'),
    'tile/white_wall_tile_bullseye': (W, 'special'),  # For symmetry

    consts.Special.BACKPANELS: 'special.behind',
    consts.Special.SQUAREBEAMS: 'special.edge',
    consts.Special.GLASS: 'special.glass',
    consts.Special.GRATING: 'special.grating',

    consts.Goo.CHEAP: 'special.goo_cheap',
}
del B, W

TEMP_TILE_PIX_SIZE = {
    # The width in texture pixels of each tile size.
    # We decrease offset to this much +- at maximum (so adjacient template
    # brushes merge with each other). This still allows creating brushes
    # with half-grid offsets.
    '4x4': 128,
    'floor': 128,  # == 4x4
    'ceiling': 128,

    '2x2': 256,

    'wall': 512,
    'special': 512,
}


class NextInstance(Exception):
    """Raised to skip to the next instance, from the SkipInstance result."""
    pass


class EndCondition(Exception):
    """Raised to skip the condition entirely, from the EndCond result."""
    pass

# Flag to indicate a result doesn't need to be exectuted anymore,
# and can be cleaned up - adding a global instance, for example.
RES_EXHAUSTED = object()


class Condition:
    __slots__ = ['flags', 'results', 'else_results', 'priority', 'source']

    def __init__(
        self,
        flags=None,
        results=None,
        else_results=None,
        priority=Decimal('0'),
        source=None,
    ):
        self.flags = flags or []
        self.results = results or []
        self.else_results = else_results or []
        self.priority = priority
        self.source = source
        self.setup()

    def __repr__(self):
        return (
            'Condition(flags={!r}, '
            'results={!r}, else_results={!r}, '
            'priority={!r}'
        ).format(
            self.flags,
            self.results,
            self.else_results,
            self.priority,
        )

    @classmethod
    def parse(cls, prop_block: Property):
        """Create a condition from a Property block."""
        flags = []
        results = []
        else_results = []
        priority = Decimal('0')
        source = None
        for prop in prop_block:
            if prop.name == 'result':
                results.extend(prop.value)  # join multiple ones together
            elif prop.name == 'else':
                else_results.extend(prop.value)
            elif prop.name == '__src__':
                # Value injected by the BEE2 export, this specifies
                # the original source of the config.
                source = prop.value

            elif prop.name in ('condition', 'switch'):
                # Shortcut to eliminate lots of Result - Condition pairs
                results.append(prop)
            elif prop.name == 'elsecondition':
                prop.name = 'condition'
                else_results.append(prop)
            elif prop.name == 'elseswitch':
                prop.name = 'switch'
                else_results.append(prop)
            elif prop.name == 'priority':
                try:
                    priority = Decimal(prop.value)
                except ArithmeticError:
                    pass
            else:
                flags.append(prop)

        return cls(
            flags,
            results,
            else_results,
            priority,
            source,
        )

    def setup(self):
        """Some results need some pre-processing before they can be used.

        """
        for res in self.results[:]:
            self.setup_result(self.results, res)

        for res in self.else_results[:]:
            self.setup_result(self.else_results, res)

    @staticmethod
    def setup_result(res_list, result):
        """Helper method to perform result setup."""
        func = RESULT_SETUP.get(result.name)
        if func:
            result.value = func(result)
            if result.value is None:
                # This result is invalid, remove it.
                res_list.remove(result)

    @staticmethod
    def test_result(inst, res):
        """Execute the given result."""
        try:
            func = RESULT_LOOKUP[res.name]
        except KeyError:
            raise ValueError('"{name}" is not a valid condition result!'.format(
                name=res.real_name,
            )) from None
        else:
            return func(inst, res)

    def test(self, inst):
        """Try to satisfy this condition on the given instance."""
        success = True
        for flag in self.flags:
            if not check_flag(flag, inst):
                success = False
                break
        results = self.results if success else self.else_results
        for res in results[:]:
            should_del = self.test_result(inst, res)
            if should_del is RES_EXHAUSTED:
                results.remove(res)


def annotation_caller(func, *parms):
    """Reorders callback arguments to the requirements of the callback.

    parms should be the unique types of arguments in the order they will be
    called with. func's arguments should be positional, and be annotated
    with the same types. A wrapper will be returned which can be called
    with the parms arguments, but delegates to func. (This could be the
    function itself).
    """
    allowed_kinds = [inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD]
    type_to_parm = dict.fromkeys(parms, None)
    sig = inspect.signature(func)
    for parm in sig.parameters.values():
        ann = parm.annotation
        if parm.kind not in allowed_kinds:
            raise ValueError('Parameter kind "{}" is not allowed!'.format(parm.kind))
        if ann is inspect.Parameter.empty:
            raise ValueError('Parameters must have value!')
        try:
            if type_to_parm[ann] is not None:
                raise ValueError('Parameter {} used twice!'.format(ann))
        except KeyError:
            raise ValueError('Unknown parameter {}'.format(ann))
        type_to_parm[ann] = parm.name
    inputs = []
    outputs = ['_'] * len(sig.parameters)
    # Parameter -> letter in func signature
    parm_order = {
        parm.name: ind
        for ind, parm in
        enumerate(sig.parameters.values())
    }
    letters = 'abcdefghijklmnopqrstuvwxyz'
    for var_name, parm in zip(letters, parms):
        inputs.append(var_name)
        out_name = type_to_parm[parm]
        if out_name is not None:
            outputs[parm_order[out_name]] = var_name

    assert '_' not in outputs

    if inputs == outputs:
        # Matches already, don't need to do anything.
        return func

    # Double function to make a closure, to allow reference to the function
    # more directly.
    # Lambdas are expressions, so we can return the result directly.
    return eval(
        '(lambda func: lambda {}: func({}))(func)'.format(
            ', '.join(inputs),
            ', '.join(outputs),
        ),
        {'func': func},
    )


def add_meta(func, priority, only_once=True):
    """Add a metacondtion, which executes a function at a priority level.

    Used to allow users to allow adding conditions before or after a
    transformation like the adding of quotes.
    """
    # This adds a condition result like "func" (with quotes), which cannot
    # be entered into property files.
    # The qualname will be unique across modules.
    name = '"' + func.__qualname__ + '"'
    LOGGER.debug("Adding metacondition ({}) with priority {!s}!",
        name,
        priority,
    )

    # Don't pass the prop_block onto the function,
    # it doesn't contain any useful data.
    RESULT_LOOKUP[name] = annotation_caller(func, Entity, Property)

    cond = Condition(
        results=[Property(name, '')],
        priority=priority,
        source='MetaCondition {}'.format(name)
    )

    if only_once:
        cond.results.append(
            Property('endCondition', '')
        )
    conditions.append(cond)
    ALL_META.append((name, priority, func))


def meta_cond(priority=0, only_once=True):
    """Decorator version of add_meta."""
    def x(func):
        add_meta(func, priority, only_once)
        return func
    return x


def make_flag(orig_name, *aliases):
    """Decorator to add flags to the lookup."""
    def x(func: Callable[[Entity, Property], bool]):
        wrapper = annotation_caller(func, Entity, Property)
        ALL_FLAGS.append(
            (orig_name, aliases, func)
        )
        FLAG_LOOKUP[orig_name.casefold()] = wrapper
        for name in aliases:
            FLAG_LOOKUP[name.casefold()] = wrapper
        return func
    return x


def make_result(orig_name, *aliases):
    """Decorator to add results to the lookup."""
    def x(func: Callable[[Entity, Property], Any]):
        wrapper = annotation_caller(func, Entity, Property)
        ALL_RESULTS.append(
            (orig_name, aliases, func)
        )
        RESULT_LOOKUP[orig_name.casefold()] = wrapper
        for name in aliases:
            RESULT_LOOKUP[name.casefold()] = wrapper
        return func
    return x


def make_result_setup(*names):
    """Decorator to do setup for this result."""
    def x(func: Callable[[Property], Any]):
        wrapper = annotation_caller(func, Property)
        for name in names:
            RESULT_SETUP[name.casefold()] = wrapper
        return func
    return x


def add(prop_block):
    """Parse and add a condition to the list."""
    con = Condition.parse(prop_block)
    if con.results or con.else_results:
        conditions.append(con)


def init(seed, inst_list, vmf_file):
    """Initialise the Conditions system."""
    # Get a bunch of values from VBSP
    global MAP_RAND_SEED, ALL_INST, VMF
    VMF = vmf_file
    MAP_RAND_SEED = seed
    ALL_INST.update(inst_list)

    # Sort by priority, where higher = done later
    zero = Decimal(0)
    conditions.sort(key=lambda cond: getattr(cond, 'priority', zero))

    build_solid_dict()
    load_templates()


def check_all():
    """Check all conditions."""
    LOGGER.info('Checking Conditions...')
    for condition in conditions:
        for inst in VMF.by_class['func_instance']:
            try:
                condition.test(inst)
            except NextInstance:
                # This is raised to immediately stop running
                # this condition, and skip to the next instance.
                pass
            except EndCondition:
                # This is raised to immediately stop running
                # this condition, and skip to the next condtion.
                break
            except:
                # Print the source of the condition if if fails...
                LOGGER.exception(
                    'Error in {}:',
                    condition.source or 'condition',
                )
                # Skip to next condition.
                import sys
                sys.exit(1)
            if not condition.results and not condition.else_results:
                break  # Condition has run out of results, quit early

    import vbsp
    LOGGER.info('Map has attributes: {}', [
        key
        for key, value in
        vbsp.settings['has_attr'].items()
        if value
    ])
    LOGGER.info('instanceLocs cache: {}', resolve_inst.cache_info())
    LOGGER.info('Style Vars: {}', dict(vbsp.settings['style_vars']))
    LOGGER.info('Global instances: {}', GLOBAL_INSTANCES)


def check_flag(flag, inst):
    LOGGER.debug(
        'Checking {} ({!s}) on {}',
        flag.real_name,
        flag.value,
        inst['file'],
    )
    name = flag.name
    # If starting with '!', invert the result.
    if name[:1] == '!':
        desired_result = False
        name = name[1:]
    else:
        desired_result = True
    try:
        func = FLAG_LOOKUP[name]
    except KeyError:
        raise ValueError(
            '"{}" is not a valid condition flag!'.format(name)
        ) from None

    res = func(inst, flag)
    return res == desired_result


def import_conditions():
    """Import all the components of the conditions package.

    This ensures everything gets registered.
    """
    import importlib
    # Find the modules in the conditions package...

    # pkgutil doesn't work when frozen, so we need to use a hardcoded list.
    try:
        from BUILD_CONSTANTS import cond_modules
        modules = cond_modules.split(';')
    except ImportError:
        import pkgutil
        modules = [
            module
            for loader, module, is_package in
            pkgutil.iter_modules(['conditions'])
        ]

    for module in modules:
        # Import the module, then discard it. The module will run add_flag
        # or add_result() functions, which save the functions into our dicts.
        # We don't need a reference to the modules themselves.
        importlib.import_module('conditions.' + module)
        LOGGER.debug('Imported {} conditions module', module)
    LOGGER.info('Imported all conditions modules!')


def build_solid_dict():
    """Build a dictionary mapping origins to brush faces.

    This allows easily finding brushes that are at certain locations.
    """
    import vbsp
    mat_types = {}
    for mat in vbsp.BLACK_PAN:
        mat_types[mat] = MAT_TYPES.black

    for mat in vbsp.WHITE_PAN:
        mat_types[mat] = MAT_TYPES.white

    for solid in VMF.brushes:
        for face in solid:
            if face.mat.casefold in consts.Goo:
                # Record all locations containing goo.
                bbox_min, bbox_max = solid.get_bbox()
                x = bbox_min.x + 64
                y = bbox_min.y + 64
                # If goo is multi-level, we want to record all pos!
                for z in range(int(bbox_min.z) + 64, int(bbox_max.z), 128):
                    GOO_LOCS[Vec_tuple(x, y, z)] = face

                # Add the location of the top face
                GOO_FACE_LOC[Vec_tuple(x, y, bbox_max.z)] = face

                # Indicate that this map contains goo...
                vbsp.settings['has_attr']['goo'] = True
                continue

            try:
                mat_type = mat_types[face.mat]
            except KeyError:
                continue
            else:
                origin = face.get_origin().as_tuple()
                if origin in SOLIDS:
                    # The only time two textures will be in the same
                    # place is if they are covering each other -
                    # nodraw them both and ignore them
                    SOLIDS.pop(origin).face.mat = consts.Tools.NODRAW
                    face.mat = consts.Tools.NODRAW
                    continue

                SOLIDS[origin] = solidGroup(
                    color=mat_type,
                    face=face,
                    solid=solid,
                    normal=face.normal(),
                )


def build_connections_dict(prop_block: Property):
    """Load in the dictionary mapping item ids to connections."""
    global TBEAM_CONN_ACT, TBEAM_CONN_DEACT

    def parse(item, key):
        """Parse the output value, handling values that aren't present."""
        val = item[key, '']
        if not val:
            return None, ''
        return Output.parse_name(val)

    for item_data in prop_block.find_key('Connections', []):
        CONNECTIONS[item_data.name] = ItemConnections(
            in_act=parse(item_data, 'input_activate'),
            in_deact=parse(item_data, 'input_deactivate'),

            out_act=parse(item_data, 'output_activate'),
            out_deact=parse(item_data, 'output_deactivate'),
        )

        if item_data.name == 'item_tbeam':
            TBEAM_CONN_ACT = parse(item_data, 'tbeam_activate')
            TBEAM_CONN_DEACT = parse(item_data, 'tbeam_deactivate')


def build_itemclass_dict(prop_block: Property):
    """Load in the dictionary mapping item classes to item ids"""
    for prop in prop_block.find_children('ItemClasses'):
        ITEM_CLASSES[prop.value.casefold()].append(prop.name)


def dump_conditions():
    """Print a list of all the condition flags, results, metaconditions

    to the screen, and then quit.
    """

    print('Dumping conditions:')
    print('-------------------')

    for lookup, name in [
            (ALL_FLAGS, 'Flags'),
            (ALL_RESULTS, 'Results'),
            ]:
        print(name + ':')
        print('-'*len(name) + '-')
        lookup.sort()
        for flag_key, aliases, func in lookup:
            print('"{}":'.format(flag_key))
            if aliases:
                print('\tAliases: "' + '", "'.join(aliases) + '"')
            dump_func_docs(func)
        input('...')
        print('')

    print('MetaConditions:')
    print('---------------')
    ALL_META.sort(key=lambda i: i[1]) # Sort by priority
    for flag_key, priority, func in ALL_META:
        print('{} ({}):'.format(flag_key, priority))
        dump_func_docs(func)
        print('')


def dump_func_docs(func):
    import inspect
    docs = inspect.getdoc(func)
    if docs:
        for line in docs.split('\n'):
            if line.strip():
                print('\t' + line.rstrip('\n'))
    else:
        print('\tNo documentation!')


def weighted_random(count: int, weights: str):
    """Generate random indexes with weights.

    This produces a list intended to be fed to random.choice(), with
    repeated indexes corresponding to the comma-separated weight values.
    """
    if weights == '':
        # Empty = equal weighting.
        return list(range(count))
    if ',' not in weights:
        LOGGER.warning('Invalid weight! ({})', weights)
        return list(range(count))

    # Parse the weight
    vals = weights.split(',')
    weight = []
    if len(vals) == count:
        for i, val in enumerate(vals):
            val = val.strip()
            if val.isdecimal():
                # repeat the index the correct number of times
                weight.extend(
                    [i] * int(val)
                )
            else:
                # Abandon parsing
                break
    if len(weight) == 0:
        LOGGER.warning('Failed parsing weight! ({!s})',weight)
        weight = list(range(count))
    # random.choice(weight) will now give an index with the correct
    # probabilities.
    return weight


def add_output(inst, prop, target):
    """Add a customisable output to an instance."""
    inst.add_out(Output(
        prop['output', ''],
        target,
        prop['input', ''],
        inst_in=prop['targ_in', ''],
        inst_out=prop['targ_out', ''],
        ))


def add_suffix(inst, suff):
    """Append the given suffix to the instance.
    """
    file = inst['file']
    old_name, dot, ext = file.partition('.')
    inst['file'] = ''.join((old_name, suff, dot, ext))


def local_name(inst: Entity, name: str):
    """Fixup the given name for inside an instance.

    This handles @names, !activator, and obeys the fixup_style option.
    """
    # If blank, keep it blank, and don't fix special or global names
    if not name or name.startswith('!') or name.startswith('@'):
        return name

    fixup = inst['fixup_style', '0']
    targ_name = inst['targetname', '']

    if fixup == '2' or not targ_name:
        # We can't do fixup..
        return name

    if fixup == '0':
        # Prefix
        return targ_name + '-' + name

    if fixup == '1':
        # Postfix
        return name + '-' + targ_name


def widen_fizz_brush(brush, thickness, bounds=None):
    """Move the two faces of a fizzler brush outward.

    This is good to make fizzlers which are thicker than 2 units.
    bounds is the output of .get_bbox(), if this should be overriden
    """

    # Subtract 2 for the fizzler width, and divide
    # to get the difference for each face.
    offset = (thickness-2)/2

    if bounds is None:
        bound_min, bound_max = brush.get_bbox()
    else:
        # Allow passing these in
        bound_min, bound_max = bounds
    origin = (bound_max + bound_min) / 2  # type: Vec
    size = bound_max - bound_min
    for axis in 'xyz':
        # One of the directions will be thinner than 32, that's the fizzler
        # direction.
        if size[axis] < 32:
            bound_min[axis] -= offset
            bound_max[axis] += offset

    for face in brush:
        # For every coordinate, set to the maximum if it's larger than the
        # origin. This will expand the two sides.
        for v in face.planes:
            for axis in 'xyz':
                if v[axis] > origin[axis]:
                    v[axis] = bound_max[axis]
                else:
                    v[axis] = bound_min[axis]


def remove_ant_toggle(toggle_ent):
    """Remove a texture_toggle instance , plus the associated antline.

    For non-toggle instances, they will just be removed.
    """
    toggle_ent.remove()

    # Assume anything with '$indicator_name' is a toggle instance
    # This will likely be called on the signs too, if present.
    overlay_name = toggle_ent.fixup['$indicator_name', '']
    if overlay_name != '':
        for ent in VMF.by_target[overlay_name]:
            ent.remove()


def reallocate_overlays(mapping: Dict[str, List[str]]):
    """Replace one side ID with others in all overlays.

    The IDs should be strings.
    """
    for overlay in VMF.by_class['info_overlay']:  # type: Entity
        sides = overlay['sides', ''].split(' ')
        for side in sides[:]:
            if side not in mapping:
                continue
            sides.remove(side)
            sides.extend(mapping[side])
        if not sides:
            # The overlay doesn't have any sides at all!
            VMF.remove_ent(overlay)
        else:
            overlay['sides'] = ' '.join(sides)


def steal_from_brush(
    temp_data: Template,
    brush_group: 'solidGroup',
    rem_brush=True,
    additional: Iterable[int]=(),
):
    """Copy IDs from a brush to a template."""
    LOGGER.info('Steal: {}', locals())

    temp_brushes = temp_data.world.copy()
    # Overlays can't be applied to entities (other than func_detail).
    if temp_data.detail is not None and temp_data.detail['classname'] == 'func_detail':
        temp_brushes.extend(temp_data.detail.solids)

    if rem_brush:
        VMF.remove_brush(brush_group.solid)
    else:
        # Switch it to nodraw if still in the map, since it must be
        # covered.
        brush_group.face.mat = 'tools/toolsnodraw'

    # Additional is a list of IDs in the template VMF, not the final one.
    additional = [
        temp_data.orig_ids.get(face_id, -1)
        for face_id in
        additional
    ]
    new_ids = []

    for brush in temp_brushes:
        for face in brush.sides:
            # Only faces pointing the same way!
            if face.normal() == brush_group.normal:
                # Skip tool brushes in the template (nodraw, player clips..)
                if face.mat.casefold().startswith('tools/'):
                    continue
                new_ids.append(str(face.id))
            # If the original ID is present in the 'additional' values
            # use it. This allows specifying specific faces.
            elif face.id in additional:
                new_ids.append(str(face.id))

    LOGGER.info('New IDS: {}', {
            str(brush_group.face.id): new_ids,
        })

    if new_ids:
        reallocate_overlays({
            str(brush_group.face.id): new_ids,
        })


def set_ent_keys(ent, inst, prop_block, block_name='Keys'):
    """Copy the given key prop block to an entity.

    This uses the keys and 'localkeys' properties on the prop_block.
    Values with $fixup variables will be treated appropriately.
    LocalKeys keys will be changed to use instance-local names, where needed.
    block_name lets you change the 'keys' suffix on the prop_block name.
    ent can be any mapping.
    """
    for prop in prop_block.find_key(block_name, []):
        ent[prop.real_name] = resolve_value(inst, prop.value)
    for prop in prop_block.find_key('Local' + block_name, []):
        if prop.value.startswith('$'):
            val = inst.fixup[prop.value]
        else:
            val = prop.value
        if val.startswith('@'):
            ent[prop.real_name] = val
        else:
            ent[prop.real_name] = local_name(inst, val)


def resolve_value(inst: Entity, value: str):
    """If a value starts with '$', lookup the associated var.

    Non-string values are passed through unchanged.
    """
    if isinstance(value, str) and value.startswith('$'):
        if value in inst.fixup:
            return inst.fixup[value]
        else:
            LOGGER.warning(
                'Invalid fixup ({}) in the "{}" instance:\n{}\n{}',
                value,
                inst['targetname'],
                inst,
                inst.fixup._fixup
            )
            return ''
    else:
        return value


def parse_temp_name(name) -> Tuple[str, Set[str]]:
    if ':' in name:
        temp_name, visgroups = name.rsplit(':', 1)
        return temp_name.casefold(), {
            vis.strip().casefold()
            for vis in
            visgroups.split(',')
        }
    else:
        return name.casefold(), set()


def load_templates():
    """Load in the template file, used for import_template()."""
    with open(TEMPLATE_LOCATION) as file:
        props = Property.parse(file, TEMPLATE_LOCATION)
    vmf = srctools.VMF.parse(props, preserve_ids=True)

    def make_subdict():
        return defaultdict(list)
    # detail_ents[temp_id][visgroup]
    detail_ents = defaultdict(make_subdict)
    world_ents = defaultdict(make_subdict)
    overlay_ents = defaultdict(make_subdict)

    for ent in vmf.by_class['bee2_template_world']:
        world_ents[
            ent['template_id'].casefold()
        ][
            ent['visgroup'].casefold()
        ].extend(ent.solids)

    for ent in vmf.by_class['bee2_template_detail']:
        detail_ents[
            ent['template_id'].casefold()
        ][
            ent['visgroup'].casefold()
        ].extend(ent.solids)

    for ent in vmf.by_class['bee2_template_overlay']:
        overlay_ents[
            ent['template_id'].casefold()
        ][
            ent['visgroup'].casefold()
        ].append(ent)

    for temp_id in set(detail_ents).union(world_ents, overlay_ents):
        world = world_ents[temp_id]
        detail = detail_ents[temp_id]
        overlay = overlay_ents[temp_id]
        visgroup_ids = set(world).union(detail, overlay)
        TEMPLATES[temp_id] = groups = {
            visgroup: (
                world[visgroup],
                detail[visgroup],
                overlay[visgroup],
            ) for visgroup in visgroup_ids
        }
        if '' not in groups:
            # We ensure the '' group is always present.
            # This is always exported later, so just make it empty.
            groups[''] = ([], [], [])


def get_template(temp_name):
    """Get the data associated with a given template.

    This is a dictionary mapping visgroups -> (world, detail, over) tuples.
    """
    try:
        return TEMPLATES[temp_name.casefold()]
    except KeyError as err:
        # Replace the KeyError with a more useful error message, and
        # list all the templates that are available.
        LOGGER.info('Templates:')
        LOGGER.info('\n'.join(
            ('* "' + temp.upper() + '"')
            for temp in
            sorted(TEMPLATES.keys())
        ))
        # Overwrite the error's value
        err.args = ('Template not found: "{}"'.format(temp_name),)
        raise err


def import_template(
        temp_name,
        origin,
        angles=None,
        targetname='',
        force_type=TEMP_TYPES.default,
        add_to_map=True,
        visgroup_choose: Callable[[Iterable[str]], Iterable[str]]=lambda x: (),
    ) -> Template:
    """Import the given template at a location.

    If force_type is set to 'detail' or 'world', all brushes will be converted
    to the specified type instead. A list of world brushes and the func_detail
    entity will be returned. If there are no detail brushes, None will be
    returned instead of an invalid entity.

    If targetname is set, it will be used to localise overlay names.
    add_to_map sets whether to add the brushes and func_detail to the map.
    visgroup_choose is a callback used to determine if visgroups should be
    added - it's passed a list of names, and should return a list of ones to use.
    """
    import vbsp
    temp_name, visgroup_ids = parse_temp_name(temp_name)
    visgroups = get_template(temp_name)
    orig_world = []  # type: List[List[Solid]]
    orig_detail = []  # type: List[List[Solid]]
    orig_over = []  # type: List[Entity]

    chosen_groups = visgroup_ids.union(visgroup_choose(
        # Skip the '' visgroup in the callback.
        filter(None, visgroups.keys()),
    ), ('', ))  # '' = no visgroup, always used.

    for group in chosen_groups:
        world, detail, over = visgroups[group]
        orig_world.append(world)
        orig_detail.append(detail)
        orig_over.extend(over)

    new_world = []  # type: List[Solid]
    new_detail = []  # type: List[Solid]
    new_over = []  # type: List[Entity]

    id_mapping = {}  # A map of the original -> new face IDs.

    for orig_lists, new_list in [
            (orig_world, new_world),
            (orig_detail, new_detail)
        ]:
        for orig_list in orig_lists:
            for old_brush in orig_list:
                brush = old_brush.copy(map=VMF, side_mapping=id_mapping, keep_vis=False)
                brush.localise(origin, angles)
                new_list.append(brush)

    for overlay in orig_over:  # type: Entity
        new_overlay = overlay.copy(
            map=VMF,
            keep_vis=False,
        )
        del new_overlay['template_id']  # Remove this, it's not part of overlays
        new_overlay['classname'] = 'info_overlay'

        sides = overlay['sides'].split()
        new_overlay['sides'] = ' '.join(
            str(id_mapping[int(side)])
            for side in sides
            if int(side) in id_mapping
        )

        srctools.vmf.localise_overlay(new_overlay, origin, angles)
        orig_target = new_overlay['targetname']

        # Only change the targetname if the overlay is not global, and we have
        # a passed name.
        if targetname and orig_target and orig_target[0] != '@':
            new_overlay['targetname'] = targetname + '-' + orig_target

        VMF.add_ent(new_overlay)
        new_over.append(new_overlay)

        # Don't let the overlays get retextured too!
        vbsp.IGNORED_OVERLAYS.add(new_overlay)

    # Don't let these get retextured normally - that should be
    # done by retexture_template(), if at all!
    for brush in new_world + new_detail:
        vbsp.IGNORED_FACES.update(brush.sides)

    if force_type is TEMP_TYPES.detail:
        new_detail.extend(new_world)
        new_world.clear()
    elif force_type is TEMP_TYPES.world:
        new_world.extend(new_detail)
        new_detail.clear()

    if add_to_map:
        VMF.add_brushes(new_world)

    if new_detail:
        detail_ent = VMF.create_ent(
            classname='func_detail'
        )
        detail_ent.solids = new_detail
        if not add_to_map:
            detail_ent.remove()
    else:
        detail_ent = None
        new_detail = []

    # Don't let these get retextured normally - that should be
    # done by retexture_template(), if at all!
    for solid in new_world:
        vbsp.IGNORED_FACES.update(solid.sides)
    for solid in new_detail:
        vbsp.IGNORED_FACES.update(solid.sides)

    return Template(new_world, detail_ent, new_over, id_mapping)


def get_scaling_template(
        temp_id: str,
    ) -> Dict[Vec_tuple, Tuple[UVAxis, UVAxis, float]]:
    """Get the scaling data from a template.

    This is a dictionary mapping normals to the U,V and rotation data.
    """
    if ':' in temp_id:
        temp_name, over_name = temp_id.split(':', 1)
    else:
        temp_name = temp_id
        over_name = ''
    world, detail, over = get_template(temp_name)[over_name]

    if detail:
        world = world + detail  # Don't mutate the lists

    uvs = {}

    for brush in world:
        for side in brush.sides:
            uvs[side.normal().as_tuple()] = (
                side.uaxis.copy(),
                side.vaxis.copy(),
                side.ham_rot,
            )

    return uvs


# 'Opposite' values for retexture_template(force_colour)
TEMP_COLOUR_INVERT = {
    MAT_TYPES.white: MAT_TYPES.black,
    MAT_TYPES.black: MAT_TYPES.white,
    None: 'INVERT',
    'INVERT': None,
}


def retexture_template(
        template_data: Template,
        origin: Vec,
        fixup: srctools.vmf.EntityFixup=None,
        replace_tex: dict= srctools.EmptyMapping,
        force_colour: MAT_TYPES=None,
        force_grid: str=None,
        use_bullseye=False,
        ):
    """Retexture a template at the given location.

    - Only textures in the TEMPLATE_RETEXTURE dict will be replaced.
    - Others will be ignored (nodraw, plasticwall, etc)
    - Wall textures pointing up and down will switch to floor/ceiling textures.
    - Textures of the same type, normal and inst origin will randomise to the
      same type.
    - replace_tex is a replacement table. This overrides everything else.
      The values should either be a list (random), or a single value.
    - If force_colour is set, all tile textures will be switched accordingly.
      If set to 'INVERT', white and black textures will be swapped.
    - If force_grid is set, all tile textures will be that size:
      ('wall', '2x2', '4x4', 'special')
    - If use_bullseye is true, the bullseye textures will be used for all panel
      sides instead of the normal textures. (This overrides force_grid.)
    - Fixup is the inst.fixup value, used to allow $replace in replace_tex.
    """
    import vbsp

    all_brushes = list(template_data.world)
    if template_data.detail is not None:
        all_brushes.extend(template_data.detail.solids)

    # Template faces are randomised per block and side. This means
    # multiple templates in the same block get the same texture, so they
    # can clip into each other without looking bad.
    rand_prefix = 'TEMPLATE_{}_{}_{}:'.format(*(origin // 128))

    # Even if not axis-aligned, make mostly-flat surfaces
    # floor/ceiling (+-40 degrees)
    # sin(40) = ~0.707
    floor_tolerance = 0.8

    can_clump = vbsp.can_clump()

    # Ensure all values are lists.
    replace_tex = {
        key.casefold(): ([value] if isinstance(value, str) else value)
        for key, value in
        replace_tex.items()
    }

    for brush in all_brushes:
        for face in brush:
            folded_mat = face.mat.casefold()

            norm = face.normal()
            random.seed(rand_prefix + norm.join('_'))

            if folded_mat in replace_tex:
                # Replace_tex overrides everything.
                mat = random.choice(replace_tex[folded_mat])
                LOGGER.info('Mat: {}, replacement: {}', folded_mat, mat)
                if mat[:1] == '$' and fixup is not None:
                    mat = fixup[mat]
                face.mat = mat
                continue

            tex_type = TEMPLATE_RETEXTURE.get(folded_mat)

            if tex_type is None:
                continue  # It's nodraw, or something we shouldn't change

            if isinstance(tex_type, str):
                # It's something like squarebeams or backpanels, just look
                # it up
                face.mat = vbsp.get_tex(tex_type)

                if tex_type == 'special.goo_cheap':
                    if face.normal() != (0, 0, 1):
                        # Goo must be facing upright!
                        # Retexture to nodraw, so a template can be made with
                        # all faces goo to work in multiple orientations.
                        face.mat = 'tools/toolsnodraw'
                    else:
                        # Goo always has the same orientation!
                        face.uaxis = UVAxis(
                            1, 0, 0,
                            offset=0,
                            scale=vbsp_options.get(float, 'goo_scale'),
                        )
                        face.vaxis = UVAxis(
                            0, -1, 0,
                            offset=0,
                            scale=vbsp_options.get(float, 'goo_scale'),
                        )
                continue
            # It's a regular wall type!
            tex_colour, grid_size = tex_type

            if force_colour == 'INVERT':
                # Invert the texture
                tex_colour = (
                    MAT_TYPES.white
                    if tex_colour is MAT_TYPES.black else
                    MAT_TYPES.black
                )
            elif force_colour is not None:
                tex_colour = force_colour

            if force_grid is not None:
                grid_size = force_grid

            if 1 in norm or -1 in norm:  # Facing NSEW or up/down
                # If axis-aligned, make the orientation aligned to world
                # That way multiple items merge well, and walls are upright.
                # We allow offsets < 1 grid tile, so items can be offset.
                face.uaxis.offset %= TEMP_TILE_PIX_SIZE[grid_size]
                face.vaxis.offset %= TEMP_TILE_PIX_SIZE[grid_size]

            if use_bullseye:
                # We want to use the bullseye textures, instead of normal
                # ones
                if norm.z < -floor_tolerance:
                    face.mat = vbsp.get_tex(
                        'special.bullseye_{}_floor'.format(tex_colour)
                    )
                elif norm.z > floor_tolerance:
                    face.mat = vbsp.get_tex(
                        'special.bullseye_{}_ceiling'.format(tex_colour)
                    )
                else:
                    face.mat = ''  # Ensure next if statement triggers

                # If those aren't defined, try the wall texture..
                if face.mat == '':
                    face.mat = vbsp.get_tex(
                        'special.bullseye_{}_wall'.format(tex_colour)
                    )
                if face.mat != '':
                    continue  # Set to a bullseye texture,
                    # don't use the wall one

            if grid_size == 'special':
                # Don't use wall on faces similar to floor/ceiling:
                if -floor_tolerance < norm.z < floor_tolerance:
                    face.mat = vbsp.get_tex(
                        'special.{!s}_wall'.format(tex_colour)
                    )
                else:
                    face.mat = ''  # Ensure next if statement triggers

                # Various fallbacks if not defined
                if face.mat == '':
                    face.mat = vbsp.get_tex(
                        'special.{!s}'.format(tex_colour)
                    )
                if face.mat == '':
                    # No special texture - use a wall one.
                    grid_size = 'wall'
                else:
                    # Set to a special texture,
                    continue # don't use the wall one

            if norm.z > floor_tolerance:
                grid_size = 'ceiling'
            if norm.z < -floor_tolerance:
                grid_size = 'floor'

            if can_clump:
                # For the clumping algorithm, set to Valve PeTI and let
                # clumping handle retexturing.
                vbsp.IGNORED_FACES.remove(face)
                if tex_colour is MAT_TYPES.white:
                    if grid_size == '4x4':
                        face.mat = 'tile/white_wall_tile003f'
                    elif grid_size == '2x2':
                        face.mat = 'tile/white_wall_tile003c'
                    else:
                        face.mat = 'tile/white_wall_tile003h'
                elif tex_colour is MAT_TYPES.black:
                    if grid_size == '4x4':
                        face.mat = 'metal/black_wall_metal_002b'
                    elif grid_size == '2x2':
                        face.mat = 'metal/black_wall_metal_002a'
                    else:
                        face.mat = 'metal/black_wall_metal_002e'
            else:
                face.mat = vbsp.get_tex(
                    '{!s}.{!s}'.format(tex_colour, grid_size)
                )

    for over in template_data.overlay[:]:
        random.seed('TEMP_OVERLAY_' + over['basisorigin'])
        mat = over['material'].casefold()
        if mat in replace_tex:
            mat = random.choice(replace_tex[mat])
            if mat[:1] == '$':
                mat = fixup[mat]
        elif mat in vbsp.TEX_VALVE:
            mat = vbsp.get_tex(vbsp.TEX_VALVE[mat])
        else:
            continue
        if mat == '':
            # If blank, remove the overlay from the map and the list.
            # (Since it's inplace, this can affect the tuple.)
            template_data.overlay.remove(over)
            over.remove()
        else:
            over['material'] = mat


def hollow_block(solid_group: solidGroup, remove_orig_face=False):
    """Convert a solid into a embeddedVoxel-style block.

    The original brushes must be in the SOLIDS dict. They will be replaced.
    This returns a dict mapping normals to the new solidGroups.
    If remove_orig_face is true, the starting face will not be kept.
    """
    import vbsp
    orig_solid = solid_group.solid  # type: Solid

    bbox_min, bbox_max = orig_solid.get_bbox()
    if 4 in (bbox_max - bbox_min):
        # If it's 4 units thick, skip hollowing - PeTI did it already.
        if remove_orig_face:
            VMF.remove_brush(orig_solid)
            del SOLIDS[solid_group.face.get_origin().as_tuple()]
        return

    VMF.remove_brush(orig_solid)

    for face in orig_solid.sides:
        if remove_orig_face and face is solid_group.face:
            # Skip readding the original face, which removes it.
            continue

        solid_key = face.get_origin().as_tuple()

        if face.mat.casefold() == 'tools/toolsnodraw' and face not in vbsp.IGNORED_FACES:
            # If it's nodraw, we can skip it. If it's also in IGNORED_FACES
            # though a condition has set it, so recreate it (it might be sealing
            # the void behind a func_detail or model).
            continue

        # Remove this face from the solids list, and get the group.
        face_group = SOLIDS.pop(solid_key, None)

        normal = face.normal()

        # Generate our new brush.
        new_brushes = import_template(
            TEMP_EMBEDDED_VOXEL,
            face.get_origin(),
            # The normal Z is swapped...
            normal.to_angle(),
            force_type=TEMP_TYPES.world,
        ).world

        # Texture the new brush..
        for brush in new_brushes:  # type: Solid
            for new_face in brush.sides:
                # The SKIP brush is the surface, all the others are nodraw.
                if new_face.mat.casefold() != 'tools/toolsskip':
                    continue

                # Overwrite all the properties, to make the new brush
                # the same as the original.
                new_face.mat = face.mat
                new_face.uaxis = face.uaxis
                new_face.vaxis = face.vaxis
                new_face.planes = face.planes
                new_face.ham_rot = 0

                # Swap the two IDs - that way when the original face gets
                # deleted the auto-set ID will vanish, leaving the original
                # ID.
                new_face.id, face.id = face.id, new_face.id

                # Remove the new face, if the original wasn't in IGNORED_FACES.
                if face not in vbsp.IGNORED_FACES:
                    vbsp.IGNORED_FACES.remove(new_face)

                # Make a new SolidGroup to match the face.
                if face_group is not None:
                    SOLIDS[solid_key] = solidGroup(
                        new_face,
                        brush,
                        face_group.normal,
                        face_group.color,
                    )


@make_flag('debug')
@make_result('debug')
def debug_flag(inst: Entity, props: Property):
    """Displays text when executed, for debugging conditions.

    If the text ends with an '=', the instance will also be displayed.
    As a flag, this always evaluates as true.
    """
    # Mark as a warning so it's more easily seen.
    if props.has_children():
        LOGGER.warning('Debug:')
        LOGGER.warning(str(props))
        LOGGER.warning(str(inst))
    elif props.value.strip().endswith('='):
        LOGGER.warning('Debug: {props}{inst!s}'.format(
            inst=inst,
            props=props.value,
        ))
    else:
        LOGGER.warning('Debug: ' + props.value)
    return True  # The flag is always true


@make_result('dummy', 'nop', 'do_nothing')
def dummy_result(inst: Entity, props: Property):
    """Dummy result that doesn't do anything."""
    pass


@meta_cond(priority=1000, only_once=False)
def remove_blank_inst(inst: Entity):
    """Remove instances with a blank file keyvalue.

    This allows conditions to strip the instances when requested.
    """
    # If editoritems instances are set to "", PeTI will autocorrect it to
    # ".vmf" - we need to handle that too.
    if inst['file', ''] in ('', '.vmf'):
        VMF.remove_ent(inst)


@meta_cond(priority=0, only_once=True)
def fix_catapult_targets(inst: Entity):
    """Set faith plate targets to transmit to clients.

    This fixes some console spam in coop, and might improve trajectories
    for faith plates.
    """
    for targ in VMF.by_class['info_target']:
        targ['spawnflags'] = '3'  # Transmit to client, ignoring PVS


@make_result_setup('timedRelay')
def res_timed_relay_setup(res: Property):
    var = res['variable', '$timer_delay']
    name = res['targetname']
    disabled = res['disabled', '0']
    flags = res['spawnflags', '0']

    final_outs = [
        Output.parse(subprop)
        for prop in res.find_all('FinalOutputs')
        for subprop in prop
    ]

    rep_outs = [
        Output.parse(subprop)
        for prop in res.find_all('RepOutputs')
        for subprop in prop
    ]

    # Never use the comma seperator in the final output for consistency.
    for out in itertools.chain(rep_outs, final_outs):
        out.comma_sep = False

    return var, name, disabled, flags, final_outs, rep_outs


@make_result('timedRelay')
def res_timed_relay(inst: Entity, res: Property):
    """Generate a logic_relay with outputs delayed by a certain amount.

    This allows triggering outputs based $timer_delay values.
    """
    var, name, disabled, flags, final_outs, rep_outs = res.value

    relay = VMF.create_ent(
        classname='logic_relay',
        spawnflags=flags,
        origin=inst['origin'],
        targetname=local_name(inst, name),
    )

    relay['StartDisabled'] = (
        inst.fixup[disabled]
        if disabled.startswith('$') else
        disabled
    )

    delay = srctools.conv_float(
        inst.fixup[var, '0']
        if var.startswith('$') else
        var
    )

    for off in range(int(math.ceil(delay))):
        for out in rep_outs:
            new_out = out.copy()  # type: Output
            new_out.target = local_name(inst, new_out.target)
            new_out.delay += off
            new_out.comma_sep = False
            relay.add_out(new_out)

    for out in final_outs:
        new_out = out.copy()  # type: Output
        new_out.target = local_name(inst, new_out.target)
        new_out.delay += delay
        new_out.comma_sep = False
        relay.add_out(new_out)


@make_result('condition')
def res_sub_condition(base_inst: Entity, res: Property):
    """Check a different condition if the outer block is true."""
    res.value.test(base_inst)
make_result_setup('condition')(Condition.parse)


@make_result('nextInstance')
def res_break():
    """Skip to the next instance.

    The value will be ignored.
    """
    raise NextInstance


@make_result('endCondition', 'nextCondition')
def res_end_condition():
    """Skip to the next condition.

    The value will be ignored.
    """
    raise EndCondition


@make_result_setup('switch')
def res_switch_setup(res: Property):
    flag = None
    method = SWITCH_TYPE.FIRST
    cases = []
    for prop in res:
        if prop.has_children():
            cases.append(prop)
        else:
            if prop.name == 'flag':
                flag = prop.value
                continue
            if prop.name == 'method':
                try:
                    method = SWITCH_TYPE(prop.value.casefold())
                except ValueError:
                    pass

    for prop in cases:
        for result in prop.value:
            Condition.setup_result(prop.value, result)

    if method is SWITCH_TYPE.LAST:
        cases[:] = cases[::-1]

    return (
        flag,
        cases,
        method,
    )


@make_result('switch')
def res_switch(inst: Entity, res: Property):
    """Run the same flag multiple times with different arguments.

    'method' is the way the search is done - first, last, random, or all.
    'flag' is the name of the flag.
    Each property group is a case to check - the property name is the flag
    argument, and the contents are the results to execute in that case.
    For 'random' mode, you can omit the flag to choose from all objects. In
    this case the flag arguments are ignored.
    """
    flag_name, cases, method = res.value

    if method is SWITCH_TYPE.RANDOM:
        cases = cases[:]
        random.shuffle(cases)

    for case in cases:
        if flag_name is not None:
            flag = Property(flag_name, case.real_name)
            if not check_flag(flag, inst):
                continue
        for res in case:
            Condition.test_result(inst, res)
        if method is not SWITCH_TYPE.ALL:
            # All does them all, otherwise we quit now.
            break


@make_result_setup('staticPiston')
def make_static_pist_setup(res: Property):
    return {
        name: resolve_inst(res[name, ''])[0]
        for name in
        (
            'bottom_1', 'bottom_2', 'bottom_3',
            'logic_0', 'logic_1', 'logic_2', 'logic_3',
            'static_0', 'static_1', 'static_2', 'static_3', 'static_4',
        )
    }


@make_result('staticPiston')
def make_static_pist(ent: Entity, res: Property):
    """Convert a regular piston into a static version.

    This is done to save entities and improve lighting.
    If changed to static pistons, the $bottom and $top level become equal.
    Instances:
        Bottom_1/2/3: Moving piston with the given $bottom_level
        Logic_0/1/2/3: Additional logic instance for the given $bottom_level
        Static_0/1/2/3/4: A static piston at the given height.
    """

    bottom_pos = ent.fixup['bottom_level', '-1']

    if (ent.fixup['connectioncount', '0'] != "0" or
            ent.fixup['disable_autodrop', '0'] != "0"):  # can it move?
        if int(bottom_pos) > 0:
            # The piston doesn't go fully down, use alt instances.
            val = res.value['bottom_' + bottom_pos]
            if val:  # Only if defined
                ent['file'] = val
        logic_file = res.value['logic_' + bottom_pos]
        if logic_file:
            # Overlay an additional logic file on top of the original
            # piston. This allows easily splitting the piston logic
            # from the styled components
            logic_ent = ent.copy()
            logic_ent['file'] = logic_file
            VMF.add_ent(logic_ent)
            # If no connections are present, set the 'enable' value in
            # the logic to True so the piston can function
            logic_ent.fixup['manager_a'] = srctools.bool_as_int(
                ent.fixup['connectioncount', '0'] == '0'
            )
    else:  # we are static
        val = res.value[
            'static_' + (
                ent.fixup['top_level', '1']
                if srctools.conv_bool(ent.fixup['start_up'], False)
                else bottom_pos
            )
        ]
        if val:
            ent['file'] = val


@make_result('GooDebris')
def res_goo_debris(res: Property):
    """Add random instances to goo squares.

    Options:
        - file: The filename for the instance. The variant files should be
            suffixed with '_1.vmf', '_2.vmf', etc.
        - space: the number of border squares which must be filled with goo
                 for a square to be eligible - defaults to 1.
        - weight, number: see the 'Variant' result, a set of weights for the
                options
        - chance: The percentage chance a square will have a debris item
        - offset: A random xy offset applied to the instances.
    """
    import brushLoc

    space = res.int('spacing', 1)
    rand_count = res.int('number', None)
    if rand_count:
        rand_list = weighted_random(
            rand_count,
            res['weights', ''],
        )
    else:
        rand_list = None
    chance = res.int('chance', 30) / 100
    file = res['file']
    offset = res.int('offset', 0)

    if file.endswith('.vmf'):
        file = file[:-4]

    goo_top_locs = {
        pos.as_tuple()
        for pos, block in
        brushLoc.POS.items()
        if block.is_goo and block.is_top
    }

    if space == 0:
        # No spacing needed, just copy
        possible_locs = [Vec(loc) for loc in goo_top_locs]
    else:
        possible_locs = []
        for x, y, z in goo_top_locs:
            # Check to ensure the neighbouring blocks are also
            # goo brushes (depending on spacing).
            for x_off, y_off in utils.iter_grid(
                min_x=-space,
                max_x=space + 1,
                min_y=-space,
                max_y=space + 1,
                stride=1,
            ):
                if x_off == y_off == 0:
                    continue  # We already know this is a goo location
                if (x + x_off, y + y_off, z) not in goo_top_locs:
                    break  # This doesn't qualify
            else:
                possible_locs.append(brushLoc.grid_to_world(Vec(x,y,z)))

    LOGGER.info(
        'GooDebris: {}/{} locations',
        len(possible_locs),
        len(goo_top_locs),
    )

    suff = ''
    for loc in possible_locs:
        random.seed('goo_debris_{}_{}_{}'.format(loc.x, loc.y, loc.z))
        if random.random() > chance:
            continue

        if rand_list is not None:
            suff = '_' + str(random.choice(rand_list) + 1)

        if offset > 0:
            loc.x += random.randint(-offset, offset)
            loc.y += random.randint(-offset, offset)
        loc.z -= 32  # Position the instances in the center of the 128 grid.
        VMF.create_ent(
            classname='func_instance',
            file=file + suff + '.vmf',
            origin=loc.join(' '),
            angles='0 {} 0'.format(random.randrange(0, 3600)/10)
        )

    return RES_EXHAUSTED


# A mapping of fizzler targetnames to the base instance
tag_fizzlers = {}
# Maps fizzler targetnames to a set of values. This is used to orient
# floor-attached signs.
tag_fizzler_locs = {}
# The value is a tuple of either ('z', x, y, z),
# ('x', x1, x2, y) or ('y', y1, y2, x).


@meta_cond(priority=-110, only_once=False)
def res_find_potential_tag_fizzlers(inst: Entity):
    """We need to know which items are 'real' fizzlers.

    This is used for Aperture Tag paint fizzlers.
    """
    if vbsp_options.get(str, 'game_id') != utils.STEAM_IDS['TAG']:
        return RES_EXHAUSTED

    if inst['file'].casefold() not in resolve_inst('<ITEM_BARRIER_HAZARD:0>'):
        return

    # The key list in the dict will be a set of all fizzler items!
    tag_fizzlers[inst['targetname']] = inst

    if tag_fizzler_locs:  # Only loop through fizzlers once.
        return

    # Determine the origins by first finding the bounding box of the brushes,
    # then averaging.
    for fizz in VMF.by_class['trigger_portal_cleanser']:
        name = fizz['targetname'][:-6]  # Strip off '_brush'
        bbox_min, bbox_max = fizz.get_bbox()
        if name in tag_fizzler_locs:
            orig_min, orig_max = tag_fizzler_locs[name]
            orig_min.min(bbox_min)
            orig_max.max(bbox_max)
        else:
            tag_fizzler_locs[name] = bbox_min, bbox_max

    for name, (s, l) in tag_fizzler_locs.items():
        # Figure out how to compare for this brush.
        # If it's horizontal, signs should point to the center:
        if abs(s.z - l.z) == 2:
            tag_fizzler_locs[name] =(
                'z',
                s.x + l.x / 2,
                s.y + l.y / 2,
                s.z + 1,
            )
            continue
        # For the vertical directions, we want to compare based on the line segment.
        if abs(s.x - l.x) == 2:  # Y direction
            tag_fizzler_locs[name] = (
                'y',
                s.y,
                l.y,
                s.x + 1,
            )
        else:  # Extends in X direction
            tag_fizzler_locs[name] = (
                'x',
                s.x,
                l.x,
                s.y + 1,
            )


@make_result('TagFizzler')
def res_make_tag_fizzler(inst: Entity, res: Property):
    """Add an Aperture Tag Paint Gun activation fizzler.

    These fizzlers are created via signs, and work very specially.
    MUST be priority -100 so it runs before fizzlers!
    """
    import vbsp
    if vbsp_options.get(str, 'game_id') != utils.STEAM_IDS['TAG']:
        # Abort - TAG fizzlers shouldn't appear in any other game!
        inst.remove()
        return

    fizz_base = fizz_name = None

    # Look for the fizzler instance we want to replace
    for targetname in inst.output_targets():
        if targetname in tag_fizzlers:
            fizz_name = targetname
            fizz_base = tag_fizzlers[targetname]
            del tag_fizzlers[targetname]  # Don't let other signs mod this one!
            continue
        else:
            # It's an indicator toggle, remove it and the antline to clean up.
            LOGGER.warning('Toggle: {}', targetname)
            for ent in VMF.by_target[targetname]:
                remove_ant_toggle(ent)
    inst.outputs.clear()  # Remove the outptuts now, they're not valid anyway.

    if fizz_base is None:
        # No fizzler - remove this sign
        inst.remove()
        return

    # The distance from origin the double signs are seperated by.
    sign_offset = res.int('signoffset', 16)

    sign_loc = (
        # The actual location of the sign - on the wall
        Vec.from_str(inst['origin']) +
        Vec(0, 0, -64).rotate_by_str(inst['angles'])
    )

    # Now deal with the visual aspect:
    # Blue signs should be on top.

    blue_enabled = srctools.conv_bool(inst.fixup['$start_enabled'])
    oran_enabled = srctools.conv_bool(inst.fixup['$start_reversed'])

    if not blue_enabled and not oran_enabled:
        # Hide the sign in this case!
        inst.remove()

    inst_angle = srctools.parse_vec_str(inst['angles'])

    inst_normal = Vec(0, 0, 1).rotate(*inst_angle)
    loc = Vec.from_str(inst['origin'])

    if blue_enabled and oran_enabled:
        inst['file'] = res['frame_double']
        # On a wall, and pointing vertically
        if inst_normal.z != 0 and Vec(0, 1, 0).rotate(*inst_angle).z != 0:
            # They're vertical, make sure blue's on top!
            blue_loc = Vec(loc.x, loc.y, loc.z + sign_offset)
            oran_loc = Vec(loc.x, loc.y, loc.z - sign_offset)
        else:
            offset = Vec(0, sign_offset, 0).rotate(*inst_angle)
            blue_loc = loc + offset
            oran_loc = loc - offset
    else:
        inst['file'] = res['frame_single']
        # They're always centered
        blue_loc = loc
        oran_loc = loc

    if inst_normal.z != 0:
        # If on floors/ceilings, rotate to point at the fizzler!
        sign_floor_loc = sign_loc.copy()
        sign_floor_loc.z = 0  # We don't care about z-positions.

        # Grab the data saved earlier in res_find_potential_tag_fizzlers()
        axis, side_min, side_max, normal = tag_fizzler_locs[fizz_name]

        # The Z-axis fizzler (horizontal) must be treated differently.
        if axis == 'z':
            # For z-axis, just compare to the center point.
            # The values are really x, y, z, not what they're named.
            sign_dir = sign_floor_loc - (side_min, side_max, normal)
        else:
            # For the other two, we compare to the line,
            # or compare to the closest side (in line with the fizz)
            other_axis = 'x' if axis == 'y' else 'y'
            if abs(sign_floor_loc[other_axis] - normal) < 32:
                # Compare to the closest side. Use ** to swap x/y arguments
                # appropriately. The closest side is the one with the
                # smallest magnitude.
                VMF.create_ent(
                    classname='info_null',
                    targetname=inst['targetname'] + '_min',
                    origin=sign_floor_loc - Vec(**{
                        axis: side_min,
                        other_axis: normal,
                    }),
                )
                VMF.create_ent(
                    classname='info_null',
                    targetname=inst['targetname'] + '_max',
                    origin=sign_floor_loc - Vec(**{
                        axis: side_max,
                        other_axis: normal,
                    }),
                )
                sign_dir = min(
                    sign_floor_loc - Vec(**{
                        axis: side_min,
                        other_axis: normal,
                    }),
                    sign_floor_loc - Vec(**{
                        axis: side_max,
                        other_axis: normal,
                    }),
                    key=Vec.mag,
                )
            else:
                # Align just based on whether we're in front or behind.
                sign_dir = Vec()
                sign_dir[other_axis] = sign_floor_loc[other_axis] - normal

        sign_angle = math.degrees(
            math.atan2(sign_dir.y, sign_dir.x)
        )
        # Round to nearest 90 degrees
        # Add 45 so the switchover point is at the diagonals
        sign_angle = (sign_angle + 45) // 90 * 90

        # Rotate to fit the instances - south is down
        sign_angle = int(sign_angle + 90) % 360
        if inst_normal.z > 0:
            sign_angle = '0 {} 0'.format(sign_angle)
        elif inst_normal.z < 0:
            # Flip upside-down for ceilings
            sign_angle = '0 {} 180'.format(sign_angle)
    else:
        # On a wall, face upright
        sign_angle = PETI_INST_ANGLE[inst_normal.as_tuple()]

    if blue_enabled:
        VMF.create_ent(
            classname='func_instance',
            file=res['blue_sign', ''],
            targetname=inst['targetname'],
            angles=sign_angle,
            origin=blue_loc.join(' '),
        )

    if oran_enabled:
        VMF.create_ent(
            classname='func_instance',
            file=res['oran_sign', ''],
            targetname=inst['targetname'],
            angles=sign_angle,
            origin=oran_loc.join(' '),
        )

    # Now modify the fizzler...

    fizz_brushes = list(
        VMF.by_class['trigger_portal_cleanser'] &
        VMF.by_target[fizz_name + '_brush']
    )

    if 'base_inst' in res:
        fizz_base['file'] = resolve_inst(res['base_inst'])[0]
    fizz_base.outputs.clear()  # Remove outputs, otherwise they break
    # branch_toggle entities

    # Subtract the sign from the list of connections, but don't go below
    # zero
    fizz_base.fixup['$connectioncount'] = str(max(
        0,
        srctools.conv_int(fizz_base.fixup['$connectioncount', ''], 0) - 1
    ))

    if 'model_inst' in res:
        model_inst = resolve_inst(res['model_inst'])[0]
        for mdl_inst in VMF.by_class['func_instance']:
            if mdl_inst['targetname', ''].startswith(fizz_name + '_model'):
                mdl_inst['file'] = model_inst

    # Find the direction the fizzler front/back points - z=floor fizz
    # Signs will associate with the given side!
    bbox_min, bbox_max = fizz_brushes[0].get_bbox()
    for axis, val in zip('xyz', bbox_max-bbox_min):
        if val == 2:
            fizz_axis = axis
            sign_center = (bbox_min[axis] + bbox_max[axis]) / 2
            break
    else:
        # A fizzler that's not 128*x*2?
        raise Exception('Invalid fizzler brush ({})!'.format(fizz_name))

    # Figure out what the sides will set values to...
    pos_blue = False
    pos_oran = False
    neg_blue = False
    neg_oran = False
    if sign_loc[fizz_axis] < sign_center:
        pos_blue = blue_enabled
        pos_oran = oran_enabled
    else:
        neg_blue = blue_enabled
        neg_oran = oran_enabled

    fizz_off_tex = {
        'left': res['off_left'],
        'center': res['off_center'],
        'right': res['off_right'],
        'short': res['off_short'],
    }
    fizz_on_tex = {
        'left': res['on_left'],
        'center': res['on_center'],
        'right': res['on_right'],
        'short': res['on_short'],
    }

    # If it activates the paint gun, use different textures
    if pos_blue or pos_oran:
        pos_tex = fizz_on_tex
    else:
        pos_tex = fizz_off_tex

    if neg_blue or neg_oran:
        neg_tex = fizz_on_tex
    else:
        neg_tex = fizz_off_tex

    if vbsp.GAME_MODE == 'COOP':
        # We need ATLAS-specific triggers
        pos_trig = VMF.create_ent(
            classname='trigger_playerteam',
        )
        neg_trig = VMF.create_ent(
            classname='trigger_playerteam',
        )
        output = 'OnStartTouchBluePlayer'
    else:
        pos_trig = VMF.create_ent(
            classname='trigger_multiple',
        )
        neg_trig = VMF.create_ent(
            classname='trigger_multiple',
            spawnflags='1',
        )
        output = 'OnStartTouch'

    pos_trig['origin'] = neg_trig['origin'] = fizz_base['origin']
    pos_trig['spawnflags'] = neg_trig['spawnflags'] = '1'  # Clients Only

    pos_trig['targetname'] = fizz_name + '-trig_pos'
    neg_trig['targetname'] = fizz_name + '-trig_neg'

    pos_trig.outputs = [
        Output(
            output,
            fizz_name + '-trig_neg',
            'Enable',
        ),
        Output(
            output,
            fizz_name + '-trig_pos',
            'Disable',
        ),
    ]

    neg_trig.outputs = [
        Output(
            output,
            fizz_name + '-trig_pos',
            'Enable',
        ),
        Output(
            output,
            fizz_name + '-trig_neg',
            'Disable',
        ),
    ]

    voice_attr = vbsp.settings['has_attr']

    if blue_enabled:
        # If this is blue/oran only, don't affect the other color
        neg_trig.outputs.append(Output(
            output,
            '@BlueIsEnabled',
            'SetValue',
            param=srctools.bool_as_int(neg_blue),
        ))
        pos_trig.outputs.append(Output(
            output,
            '@BlueIsEnabled',
            'SetValue',
            param=srctools.bool_as_int(pos_blue),
        ))
        # Add voice attributes - we have the gun and gel!
        voice_attr['bluegelgun'] = True
        voice_attr['bluegel'] = True
        voice_attr['bouncegun'] = True
        voice_attr['bouncegel'] = True

    if oran_enabled:
        neg_trig.outputs.append(Output(
            output,
            '@OrangeIsEnabled',
            'SetValue',
            param=srctools.bool_as_int(neg_oran),
        ))
        pos_trig.outputs.append(Output(
            output,
            '@OrangeIsEnabled',
            'SetValue',
            param=srctools.bool_as_int(pos_oran),
        ))
        voice_attr['orangegelgun'] = True
        voice_attr['orangegel'] = True
        voice_attr['speedgelgun'] = True
        voice_attr['speedgel'] = True

    if not oran_enabled and not blue_enabled:
        # If both are disabled, we must shutdown the gun when touching
        # either side - use neg_trig for that purpose!
        # We want to get rid of pos_trig to save ents
        VMF.remove_ent(pos_trig)
        neg_trig['targetname'] = fizz_name + '-trig'
        neg_trig.outputs.clear()
        neg_trig.add_out(Output(
            output,
            '@BlueIsEnabled',
            'SetValue',
            param='0'
        ))
        neg_trig.add_out(Output(
            output,
            '@OrangeIsEnabled',
            'SetValue',
            param='0'
        ))

    for fizz_brush in fizz_brushes:  # portal_cleanser ent, not solid!
        # Modify fizzler textures
        bbox_min, bbox_max = fizz_brush.get_bbox()
        for side in fizz_brush.sides():
            norm = side.normal()
            if norm[fizz_axis] == 0:
                # Not the front/back: force nodraw
                # Otherwise the top/bottom will have the odd stripes
                # which won't match the sides
                side.mat = 'tools/toolsnodraw'
                continue
            if norm[fizz_axis] == 1:
                side.mat = pos_tex[
                    vbsp.TEX_FIZZLER[
                        side.mat.casefold()
                    ]
                ]
            else:
                side.mat = neg_tex[
                    vbsp.TEX_FIZZLER[
                        side.mat.casefold()
                    ]
                ]
        # The fizzler shouldn't kill cubes
        fizz_brush['spawnflags'] = '1'

        fizz_brush.outputs.append(Output(
            output,
            '@shake_global',
            'StartShake',
        ))

        fizz_brush.outputs.append(Output(
            output,
            '@shake_global_sound',
            'PlaySound',
        ))

        # The triggers are 8 units thick, 24 from the center
        # (-1 because fizzlers are 2 thick on each side).
        neg_min, neg_max = Vec(bbox_min), Vec(bbox_max)
        neg_min[fizz_axis] -= 23
        neg_max[fizz_axis] -= 17

        pos_min, pos_max = Vec(bbox_min), Vec(bbox_max)
        pos_min[fizz_axis] += 17
        pos_max[fizz_axis] += 23

        if blue_enabled or oran_enabled:
            neg_trig.solids.append(
                VMF.make_prism(
                    neg_min,
                    neg_max,
                    mat='tools/toolstrigger',
                ).solid,
            )
            pos_trig.solids.append(
                VMF.make_prism(
                    pos_min,
                    pos_max,
                    mat='tools/toolstrigger',
                ).solid,
            )
        else:
            # If neither enabled, use one trigger
            neg_trig.solids.append(
                VMF.make_prism(
                    neg_min,
                    pos_max,
                    mat='tools/toolstrigger',
                ).solid,
            )
