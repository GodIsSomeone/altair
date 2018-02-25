# The contents of this file are automatically written by
# tools/generate_schema_wrapper.py. Do not modify directly.
# 2018-02-24 22:32
import collections
import json

import jsonschema


class UndefinedType(object):
    """A singleton object for marking undefined attributes"""
    __instance = None
    def __new__(cls, *args, **kwargs):
        if not isinstance(cls.__instance, cls):
            cls.__instance = object.__new__(cls, *args, **kwargs)
        return cls.__instance
    def __repr__(self):
        return 'Undefined'
Undefined = UndefinedType()


class SchemaBase(object):
    """Base class for schema wrappers.

    Each derived class should set the _json_schema class attribute which is
    used for validation. If not specified in the class definition, an
    appropriate __init__ function will be generated by SchemaBaseMeta.
    """
    _schema = {}
    _rootschema = None

    def __init__(self, *args, **kwds):
        # Two valid options for initialization, which should be handled by
        # derived classes:
        # - a single arg with no kwds, for, e.g. {'type': 'string'}
        # - zero args with zero or more kwds for {'type': 'object'}
        if kwds:
            assert len(args) == 0
        else:
            assert len(args) in [0, 1]

        # use object.__setattr__ because we override setattr below.
        object.__setattr__(self, '_args', args)
        object.__setattr__(self, '_kwds', kwds)

    def copy(self, deep=True, ignore=()):
        """Return a copy of the object

        Parameters
        ----------
        deep : boolean, optional
            if True (default) then return a deep copy of all dict, list, and
            SchemaBase objects within the object structure
        ignore : list, optional
            A list of keys for which the contents should not be copied, but
            only stored by reference.
        """
        def _deep_copy(obj, ignore=()):
            if isinstance(obj, SchemaBase):
                args = tuple(_deep_copy(arg) for arg in obj._args)
                kwds = {k: (_deep_copy(v, ignore=ignore)
                            if k not in ignore else v)
                        for k, v in obj._kwds.items()}
                return obj.__class__(*args, **kwds)
            elif isinstance(obj, list):
                return [_deep_copy(v, ignore=ignore) for v in obj]
            elif isinstance(obj, dict):
                return {k: (_deep_copy(v, ignore=ignore)
                            if k not in ignore else v)
                        for k, v in obj.items()}
            else:
                return obj
        if deep:
            return _deep_copy(self, ignore=ignore)
        else:
            return self.__class__(*self._args, **self._kwds)

    def __getattr__(self, attr):
        # reminder: getattr is called after the normal lookups
        if attr in self._kwds:
            return self._kwds[attr]
        else:
            try:
                _getattr = super(SchemaBase, self).__getattr__
            except AttributeError:
                _getattr = super(SchemaBase, self).__getattribute__
            return _getattr(attr)

    def __setattr__(self, item , val):
        self._kwds[item] = val

    def __getitem__(self, item):
        return self._kwds[item]

    def __setitem__(self, item, val):
        self._kwds[item] = val

    def __repr__(self):
        if self._kwds:
            args = ("{0}: {1!r}".format(key, val)
                    for key, val in self._kwds.items()
                    if val is not Undefined)
            args = '\n' + ',\n'.join(args)
            return "{0}({{{1}\n}})".format(self.__class__.__name__,
                                            args.replace('\n', '\n  '))
        else:
            return "{0}({1!r})".format(self.__class__.__name__, self._args[0])

    def to_dict(self, validate=True, ignore=[], context={}):
        """Return a dictionary representation of the object

        Parameters
        ----------
        validate : boolean
            If True (default), then validate the output dictionary
            against the schema.
        ignore : list
            A list of keys to ignore. This will *not* passed to child to_dict
            function calls.
        context : dict (optional)
            A context dictionary that will be passed to all child to_dict
            function calls

        Returns
        -------
        dct : dictionary
            The dictionary representation of this object

        Raises
        ------
        jsonschema.ValidationError :
            if validate=True and the dict does not conform to the schema
        """
        def _todict(val):
            if isinstance(val, SchemaBase):
                # only validate at the top level
                return val.to_dict(validate=False, context=context)
            elif isinstance(val, list):
                return [_todict(v) for v in val]
            elif isinstance(val, dict):
                return {k: _todict(v) for k, v in val.items()
                        if v is not Undefined}
            else:
                return val

        if self._args and not self._kwds:
            result = _todict(self._args[0])
        elif not self._args:
            result = _todict({k: v for k, v in self._kwds.items()
                              if k not in ignore})
        else:
            raise ValueError("{0} instance has both a value and properties : "
                             "cannot serialize to dict".format(self.__class__))
        if validate:
            self.validate(result)
        return result

    @classmethod
    def from_dict(cls, dct, validate=True):
        """Construct class from a dictionary representation

        Parameters
        ----------
        dct : dictionary
            The dict from which to construct the class
        validate : boolean
            If True (default), then validate the input against the schema.

        Returns
        -------
        obj : Schema object
            The wrapped schema

        Raises
        ------
        jsonschema.ValidationError :
            if validate=True and dct does not conform to the schema
        """
        if validate:
            cls.validate(dct)
        converter = _FromDict(SchemaBase.__subclasses__())
        return converter.from_dict(constructor=cls, root=cls,
                                   schema=cls._schema, dct=dct)

    @classmethod
    def validate(cls, instance, schema=None):
        """
        Validate the instance against the class schema in the context of the
        rootschema.
        """
        if schema is None:
            schema = cls._schema
        resolver = jsonschema.RefResolver.from_schema(cls._rootschema or cls._schema)
        return jsonschema.validate(instance, schema, resolver=resolver)

    @classmethod
    def resolve_references(cls, schema):
        """Resolve references of the schema the context of this object's schema"""
        resolver = jsonschema.RefResolver.from_schema(cls._rootschema
                                                      or cls._schema
                                                      or schema)
        while '$ref' in schema:
            ref, schema = resolver.resolve(schema['$ref'])
        return schema

    def __dir__(self):
        return list(self._kwds.keys())


class _FromDict(object):
    """Class used to construct SchemaBase class hierarchies from a dict

    The primary purpose of using this class is to be able to build a hash table
    that maps schemas to their wrapper classes. The candidate classes are
    specified in the ``class_list`` argument to the constructor.
    """
    _hash_exclude_keys = ('definitions', 'title', 'description', '$schema', 'id')

    def __init__(self, class_list):
        # Create a mapping of a schema hash to a list of matching classes
        # This lets us quickly determine the correct class to construct
        self.class_dict = collections.defaultdict(list)
        for cls in class_list:
            self.class_dict[self.hash_schema(cls._schema)].append(cls)

    @classmethod
    def hash_schema(cls, schema, use_json=True):
        """
        Compute a python hash for a nested dictionary which
        properly handles dicts, lists, sets, and tuples.

        At the top level, the function excludes from the hashed schema all keys
        listed in `exclude_keys`.

        This implements two methods: one based on conversion to JSON, and one based
        on recursive conversions of unhashable to hashable types; the former seems
        to be slightly faster in several benchmarks.
        """
        if cls._hash_exclude_keys:
            schema = {key: val for key, val in schema.items()
                      if key not in cls._hash_exclude_keys}
        if use_json:
            s = json.dumps(schema, sort_keys=True)
            return hash(s)
        else:
            def _freeze(val):
                if isinstance(val, dict):
                    return frozenset((k, _freeze(v)) for k, v in val.items())
                elif isinstance(val, set):
                    return frozenset(map(_freeze, val))
                elif isinstance(val, list) or isinstance(val, tuple):
                    return tuple(map(_freeze, val))
                else:
                    return val
            return hash(_freeze(schema))

    @staticmethod
    def _passthrough(*args, **kwds):
        """An object constructor that simply passes arguments through"""
        if kwds and not args:
            return kwds
        elif args and not kwds:
            assert len(args) == 1
            return args[0]
        else:
            raise ValueError("Both args and kwds supplied")

    def from_dict(self, constructor, root, schema, dct):
        """Construct an object from a dict representation"""
        # TODO: introspect lists, objects, etc. when they don't have a wrapper.
        #       could do this by passing the schema rather than cls.
        schema = root.resolve_references(schema)

        def _get_constructor(schema):
            # TODO: do something more than simply selecting the last match?
            hash_ = self.hash_schema(schema)
            matches = self.class_dict[hash_]
            constructor = matches[-1] if matches else self._passthrough
            schema = root.resolve_references(schema)
            return constructor, schema

        if 'anyOf' in schema or 'oneOf' in schema:
            schemas = schema.get('anyOf', []) + schema.get('oneOf', [])
            for this_schema in schemas:
                this_constructor, this_schema = _get_constructor(this_schema)
                try:
                    root.validate(dct, this_schema)
                except jsonschema.ValidationError:
                    continue
                else:
                    return self.from_dict(this_constructor, root, this_schema, dct)

        if isinstance(dct, dict):
            # TODO: handle schemas for additionalProperties/patternProperties
            props = schema.get('properties', {})
            kwds = {}
            for key, val in dct.items():
                if key in props:
                    prop_constructor, prop_schema = _get_constructor(props[key])
                    val = self.from_dict(prop_constructor, root, prop_schema, val)
                kwds[key] = val
            return constructor(**kwds)

        elif isinstance(dct, list):
            if 'items' in schema:
                item_schema = schema['items']
                item_constructor, item_schema = _get_constructor(item_schema)
            else:
                item_schema = {}
                item_constructor = self._passthrough
            dct = [self.from_dict(item_constructor, root, item_schema, val)
                   for val in dct]
            return constructor(dct)
        else:
            return constructor(dct)
