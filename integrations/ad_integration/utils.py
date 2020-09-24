from collections.abc import Mapping


class AttrDict(dict):
    """Enable dot.notation access for a dict object.

    Example:
        script_result = AttrDict({"exit_code": 0})
        self.assertEqual(script_result.exit_code, 0)
    """

    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def recursive_dict_update(original, updates):
    """Recursively update 'original' with keys from 'updates'.

    Example:
        original = {'alfa': {'beta': 2, 'charlie': 3}},
        updates = {'alfa': {'beta': 4}}
        # Non recursive update
        updated = {**original, **updates}
        self.assertEqual(updated, {'alfa': {'beta': 4}})
        # Recursive update
        r_updated = recursive_dict_update(original, updates)
        self.assertEqual(r_updated, {'alfa': {'beta': 4, 'charlie': 3}})

    Returns:
        dict: modified 'original'
    """
    for key, value in updates.items():
        if isinstance(value, Mapping):
            original[key] = recursive_dict_update(original.get(key, {}), value)
        else:
            original[key] = value
    return original


def dict_map(func, dicty):
    """Map the dict values.

    Example:
        input_dict = {1: 1, 2: 2, 3: 3}
        output_dict = dict_map(lambda value: value ** 2, input_dict)
        self.assertEqual(output_dict, {1: 1, 2: 4, 3: 6})

    Returns:
        dict: A dict where func has been applied to every value.
    """
    return {key: func(value, key=key) for key, value in dicty.items()}


def dict_partition(func, dicty):
    """Partition the input dict into two using the predicate function.

    Example:
        input_dict = {0: 'value', 1: 'value': 2: 'value'}
        odd_dict, even_dict = dict_partition(
            lambda key, value: value % 2 == 0, input_dict
        )
        self.assertEqual(odd_dict, {1: 'value'})
        self.assertEqual(even_dict, {0: 'value', 2: 'value'})

    Returns:
        (dict, dict): A dict containing key-value pairs that failed the
                      predicate and a dict containing the key-value pairs
                      that passed the predicate.
    """
    falsy, truesy = {}, {}
    for key, value in dicty.items():
        write_dict = truesy if func(key, value) else falsy
        write_dict[key] = value
    return falsy, truesy


def duplicates(iterable):
    """Return set of duplicates from iterable.

    Example:
        input_list = [1, 5, 2, 4, 2, 1]
        dup_set = duplicates(input_list)
        self.assertEqual(dup_set, {1, 2})

    Returns:
        set: A set of the elements which are duplicates.
    """
    seen = set()
    return set(x for x in iterable if x in seen or seen.add(x))
