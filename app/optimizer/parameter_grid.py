from itertools import product


class ParameterGrid:
    def __init__(self, parameters):
        if not parameters:
            raise ValueError("Parameter grid cannot be empty")
        self.parameters = {name: tuple(values) for name, values in parameters.items()}
        if any(not values for values in self.parameters.values()):
            raise ValueError("Every parameter must have at least one candidate value")

    def combinations(self):
        names = tuple(self.parameters)
        for values in product(*(self.parameters[name] for name in names)):
            yield dict(zip(names, values))

    def __len__(self):
        result = 1
        for values in self.parameters.values():
            result *= len(values)
        return result
