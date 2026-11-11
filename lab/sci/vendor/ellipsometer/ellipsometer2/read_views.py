"""Per-Store derived views, isolated from consumers that edit returned objects."""
from functools import wraps
from inspect import signature


def cached_view(function):
    parameters=signature(function)
    name=function.__module__+'.'+function.__qualname__
    @wraps(function)
    def cached(store,*args,**kwargs):
        cache=getattr(store,'read_view',None)
        if cache is None:  # Lightweight adapters used by core/reference tests.
            return function(store,*args,**kwargs)
        bound=parameters.bind(store,*args,**kwargs)
        bound.apply_defaults()
        values=dict(bound.arguments);values.pop(next(iter(values)))
        return cache(name,values,lambda:function(store,*args,**kwargs))
    return cached
