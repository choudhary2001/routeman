"""
Turn validation classes into routeman Fields without any documentation library:
DRF serializers, Django forms, marshmallow schemas and pydantic models.
"""
from __future__ import annotations

import inspect
from typing import List, Optional

from .model import NO_DEFAULT, Field


def _fit_text(f: Field, min_length=None, max_length=None):
    from .examples import example
    if f.example is not NO_DEFAULT or f.default is not NO_DEFAULT or f.choices or f.type != 'string':
        return
    value = example(f)
    if not isinstance(value, str):
        return
    if max_length and len(value) > max_length:
        f.example = value[:max_length]
    elif min_length and len(value) < min_length:
        f.example = (value * (min_length // max(len(value), 1) + 1))[:min_length]


def _fit_number(f: Field, min_value=None, max_value=None):
    from .examples import example
    if f.example is not NO_DEFAULT or f.default is not NO_DEFAULT or f.type not in ('integer', 'number'):
        return
    value = example(f)
    if min_value is not None and value < min_value:
        f.example = min_value
    if max_value is not None and value > max_value:
        f.example = max_value


def fields_of(obj) -> Optional[List[Field]]:
    """Fields of any supported validation class/instance, or None if it is not one."""
    cls = obj if inspect.isclass(obj) else type(obj)
    try:
        mro = cls.__mro__
    except AttributeError:
        return None
    names = {c.__name__ for c in mro}
    libs = {(c.__module__ or '').split('.')[0] for c in mro}
    try:
        if 'rest_framework' in libs and 'BaseSerializer' in names:
            return drf_fields(obj)
        if 'django' in libs and 'BaseForm' in names:
            return form_fields(obj)
        if 'marshmallow' in libs and 'Schema' in names:
            return marshmallow_fields(obj)
        if 'pydantic' in libs and 'BaseModel' in names:
            return pydantic_fields(obj)
    except Exception:  # noqa: BLE001 - a class we cannot introspect is reported as unknown
        return None
    return None


def has_files(fields: List[Field]) -> bool:
    return any(f.has_file for f in fields)


# --- Django REST framework ----------------------------------------------------------------

def drf_fields(serializer, context=None, depth=0) -> List[Field]:
    if inspect.isclass(serializer):
        try:
            serializer = serializer(context=context or {})
        except Exception:  # noqa: BLE001 - serializers that need arguments
            serializer = serializer.__new__(serializer)
            serializer.__init__()
    out = []
    for name, f in serializer.fields.items():
        field = drf_field(name, f, depth)
        if field is not None:
            out.append(field)
    return out


def _pk_type(queryset) -> str:
    try:
        pk = queryset.model._meta.pk
        internal = pk.get_internal_type()
    except Exception:  # noqa: BLE001
        return 'integer'
    if internal == 'UUIDField':
        return 'uuid'
    if internal in ('CharField', 'SlugField', 'TextField'):
        return 'string'
    return 'integer'


def drf_field(name, f, depth=0) -> Optional[Field]:
    from rest_framework import fields as df, relations as dr, serializers as ds
    from rest_framework.fields import empty

    if getattr(f, 'read_only', False) or isinstance(f, (df.HiddenField, df.SerializerMethodField)):
        return None
    out = Field(name=name, required=bool(getattr(f, 'required', False)),
                description=str(getattr(f, 'help_text', '') or ''))
    default = getattr(f, 'default', empty)
    if default is not empty and not callable(default):
        out.default = default

    if isinstance(f, ds.ListSerializer):
        out.type = 'array'
        out.item = Field(name=name, type='object', children=drf_fields(f.child, depth=depth + 1) if depth < 5 else [])
    elif isinstance(f, ds.BaseSerializer):
        out.type = 'object'
        out.children = drf_fields(f, depth=depth + 1) if depth < 5 else []
    elif isinstance(f, dr.ManyRelatedField):
        out.type = 'array'
        out.item = drf_field(name, f.child_relation, depth) or Field(name=name)
        out.item.required = False
    elif isinstance(f, dr.PrimaryKeyRelatedField):
        out.type = drf_field('pk', f.pk_field, depth).type if getattr(f, 'pk_field', None) else _pk_type(f.queryset)
    elif isinstance(f, dr.HyperlinkedRelatedField):
        out.type = 'url'
    elif isinstance(f, dr.SlugRelatedField):
        out.type = 'string'
    elif isinstance(f, df.MultipleChoiceField):
        out.type = 'array'
        out.item = Field(name=name, choices=[k for k in f.choices if k not in ('', None)])
    elif isinstance(f, df.ChoiceField):
        out.choices = [k for k in f.choices if k not in ('', None)]
        out.type = {int: 'integer', float: 'number', bool: 'boolean'}.get(
            type(out.choices[0]) if out.choices else str, 'string')
    elif isinstance(f, df.ListField):
        out.type = 'array'
        child = drf_field(name, f.child, depth) if getattr(f, 'child', None) is not None else None
        out.item = child or Field(name=name)
        if f.min_length and f.min_length > 1:
            from .examples import example
            out.example = [example(out.item)] * f.min_length
    elif isinstance(f, (df.DictField, df.JSONField, getattr(df, 'HStoreField', df.DictField))):
        out.type = 'object'
        out.children = []
    elif isinstance(f, df.FileField):  # ImageField subclasses it
        out.type = 'file'
    elif isinstance(f, df.BooleanField) or type(f).__name__ == 'NullBooleanField':
        out.type = 'boolean'
    elif isinstance(f, df.IntegerField):
        out.type = 'integer'
        _fit_number(out, f.min_value, f.max_value)
    elif isinstance(f, (df.FloatField, df.DecimalField)):
        out.type = 'number'
        _fit_number(out, getattr(f, 'min_value', None), getattr(f, 'max_value', None))
    elif isinstance(f, df.DateTimeField):
        out.type = 'datetime'
    elif isinstance(f, df.DateField):
        out.type = 'date'
    elif isinstance(f, df.TimeField):
        out.type = 'time'
    elif isinstance(f, df.DurationField):
        out.example = '01:00:00'
    elif isinstance(f, df.UUIDField):
        out.type = 'uuid'
    elif isinstance(f, df.EmailField):
        out.type = 'email'
    elif isinstance(f, df.URLField):
        out.type = 'url'
    elif isinstance(f, df.IPAddressField):
        out.example = '127.0.0.1'
    elif isinstance(f, df.CharField):
        out.type = 'string'
        _fit_text(out, getattr(f, 'min_length', None), getattr(f, 'max_length', None))
    else:
        out.type = 'any'
    return out


# --- Django forms ---------------------------------------------------------------------------

def form_fields(form) -> List[Field]:
    cls = form if inspect.isclass(form) else type(form)
    base = getattr(form, 'fields', None) if not inspect.isclass(form) else None
    items = (base or getattr(cls, 'base_fields', {})).items()
    return [form_field(name, f) for name, f in items if not getattr(f, 'disabled', False)]


def form_field(name, f) -> Field:
    from django import forms as dj

    out = Field(name=name, required=bool(f.required), description=str(f.help_text or ''))
    initial = f.initial
    if initial is not None and not callable(initial):
        out.default = initial
    if isinstance(f, dj.ModelMultipleChoiceField):
        out.type = 'array'
        out.item = Field(name=name, type=_pk_type(f.queryset))
    elif isinstance(f, dj.ModelChoiceField):
        out.type = _pk_type(f.queryset)
    elif isinstance(f, dj.MultipleChoiceField):
        out.type = 'array'
        out.item = Field(name=name, choices=[k for k, _ in f.choices if k not in ('', None)])
    elif isinstance(f, dj.ChoiceField):
        out.choices = [k for k, _ in f.choices if k not in ('', None)]
    elif isinstance(f, dj.FileField):
        out.type = 'file'
    elif isinstance(f, (dj.BooleanField, dj.NullBooleanField)):
        out.type = 'boolean'
    elif isinstance(f, dj.IntegerField):
        out.type = 'integer'
        _fit_number(out, f.min_value, f.max_value)
    elif isinstance(f, (dj.FloatField, dj.DecimalField)):
        out.type = 'number'
        _fit_number(out, f.min_value, f.max_value)
    elif isinstance(f, dj.DateTimeField):
        out.type = 'datetime'
        out.example = '2026-01-31 10:00:00'
    elif isinstance(f, dj.DateField):
        out.type = 'date'
    elif isinstance(f, dj.TimeField):
        out.type = 'time'
    elif isinstance(f, dj.UUIDField):
        out.type = 'uuid'
    elif isinstance(f, dj.EmailField):
        out.type = 'email'
    elif isinstance(f, dj.URLField):
        out.type = 'url'
    elif isinstance(f, dj.JSONField):
        out.type = 'object'
        out.children = []
    elif isinstance(f, dj.CharField):
        _fit_text(out, f.min_length, f.max_length)
    return out


# --- marshmallow ----------------------------------------------------------------------------

def marshmallow_fields(schema, depth=0) -> List[Field]:
    if inspect.isclass(schema):
        schema = schema()
    out = []
    # Source order: every marshmallow field records when it was created
    items = sorted(schema.fields.items(), key=lambda kv: getattr(kv[1], '_creation_index', 0))
    for name, f in items:
        if getattr(f, 'dump_only', False):
            continue
        out.append(marshmallow_field(getattr(f, 'data_key', None) or name, f, depth))
    return out


def marshmallow_field(name, f, depth=0) -> Field:
    from marshmallow import fields as mf, validate as mv

    out = Field(name=name, required=bool(f.required))
    meta = getattr(f, 'metadata', {}) or {}
    out.description = str(meta.get('description', '') or '')
    for attr in ('load_default', 'missing'):
        value = getattr(f, attr, None)
        if value is not None and not callable(value) and type(value).__name__ != '_Missing':
            out.default = value
            break
    lengths = {}
    for v in getattr(f, 'validators', []) or []:
        if isinstance(v, mv.OneOf):
            out.choices = list(v.choices)
        elif isinstance(v, mv.Length):
            lengths = {'min_length': v.min, 'max_length': v.max}
        elif isinstance(v, mv.Range):
            lengths = {'min_value': v.min, 'max_value': v.max}
    if isinstance(f, mf.Nested):
        nested = f.schema
        children = marshmallow_fields(nested, depth + 1) if depth < 5 else []
        if getattr(f, 'many', False):
            out.type, out.item = 'array', Field(name=name, type='object', children=children)
        else:
            out.type, out.children = 'object', children
    elif isinstance(f, mf.List):
        out.type = 'array'
        out.item = marshmallow_field(name, f.inner, depth)
        out.item.required = False
    elif isinstance(f, (mf.Dict, getattr(mf, 'Mapping', mf.Dict))):
        out.type, out.children = 'object', []
    elif hasattr(mf, 'Enum') and isinstance(f, mf.Enum):
        out.choices = [m.value if getattr(f, 'by_value', False) else m.name for m in f.enum]
    elif isinstance(f, mf.Boolean):
        out.type = 'boolean'
    elif isinstance(f, mf.Integer):
        out.type = 'integer'
        _fit_number(out, lengths.get('min_value'), lengths.get('max_value'))
    elif isinstance(f, (mf.Float, mf.Decimal, mf.Number)):
        out.type = 'number'
        _fit_number(out, lengths.get('min_value'), lengths.get('max_value'))
    elif isinstance(f, mf.Date):  # before DateTime: older marshmallow derives Date from DateTime
        out.type = 'date'
    elif isinstance(f, mf.Time):
        out.type = 'time'
    elif isinstance(f, mf.DateTime):
        out.type = 'datetime'
    elif isinstance(f, mf.UUID):
        out.type = 'uuid'
    elif isinstance(f, mf.Email):
        out.type = 'email'
    elif isinstance(f, mf.Url):
        out.type = 'url'
    elif isinstance(f, mf.String):
        _fit_text(out, lengths.get('min_length'), lengths.get('max_length'))
    elif isinstance(f, mf.Raw):
        out.type = 'any'
    return out


# --- pydantic -------------------------------------------------------------------------------

def pydantic_fields(model) -> List[Field]:
    from .openapi import fields_from_json_schema
    cls = model if inspect.isclass(model) else type(model)
    schema = cls.model_json_schema() if hasattr(cls, 'model_json_schema') else cls.schema(
        ref_template='#/definitions/{model}')
    return fields_from_json_schema(schema)
