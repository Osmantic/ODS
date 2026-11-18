// Just enough of TypeBox's builders to register tools; schemas are not validated here.
const scalar = (type) => (options = {}) => ({ type, ...options });

export const Type = {
  Object: (properties) => ({ type: "object", properties }),
  String: scalar("string"),
  Integer: scalar("integer"),
  Boolean: scalar("boolean"),
  Array: (items, options = {}) => ({ type: "array", items, ...options }),
  Optional: (schema) => ({ ...schema, optional: true }),
  Union: (anyOf) => ({ anyOf }),
  Literal: (value) => ({ const: value }),
};
