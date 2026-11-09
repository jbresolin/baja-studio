/**
 * A form generated from a settings JSON Schema.
 *
 * Booleans become checkboxes, numbers number inputs, enums dropdowns, fields
 * marked `secret` password inputs, a nested model (the gateway's `tls`) a group
 * of those, and anything more complex (lists) a JSON text box.
 * Fields marked `managed_elsewhere` are left out: their module's page edits them.
 * Later work packages can add field kinds here and every Settings page gains them.
 */

import { useState } from "react";

import type { JsonSchemaProperty, SettingsSchema, SettingsValues } from "../lib/api";

interface SchemaFormProps {
  schema: SettingsSchema;
  values: SettingsValues;
  fieldErrors: Record<string, string>;
  onChange: (field: string, value: unknown) => void;
}

/** Follow `$ref` and unwrap `anyOf: [X, null]` (optional fields) to the useful definition. */
export function resolveProperty(
  schema: SettingsSchema,
  property: JsonSchemaProperty,
): JsonSchemaProperty {
  if (property.$ref) {
    const name = property.$ref.split("/").pop() ?? "";
    const target = schema.$defs?.[name] ?? {};
    return { ...target, ...property, $ref: undefined };
  }
  if (property.anyOf) {
    const nonNull = property.anyOf.find((option) => option.type !== "null");
    if (nonNull) return { ...resolveProperty(schema, nonNull), ...property, anyOf: undefined };
  }
  return property;
}

export function SchemaForm({ schema, values, fieldErrors, onChange }: SchemaFormProps) {
  return (
    <div className="schema-form">
      {Object.entries(schema.properties)
        .filter(([, rawProperty]) => !rawProperty.managed_elsewhere)
        .map(([field, rawProperty]) => {
          const property = resolveProperty(schema, rawProperty);
          const inputId = `setting-${field}`;
          return (
            <div className="field" key={field}>
              <label htmlFor={inputId}>{property.title ?? field}</label>
              <FieldInput
                id={inputId}
                schema={schema}
                property={property}
                value={values[field]}
                onChange={(value) => onChange(field, value)}
              />
              {property.description && <div className="field-help">{property.description}</div>}
              {fieldErrors[field] && <div className="field-error">{fieldErrors[field]}</div>}
            </div>
          );
        })}
    </div>
  );
}

interface FieldInputProps {
  id: string;
  schema: SettingsSchema;
  property: JsonSchemaProperty;
  value: unknown;
  onChange: (value: unknown) => void;
}

function FieldInput({ id, schema, property, value, onChange }: FieldInputProps) {
  if (property.enum) {
    return (
      <select
        id={id}
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
      >
        {property.enum.map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  }
  switch (property.type) {
    case "boolean":
      return (
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
      );
    case "integer":
    case "number":
      return (
        <input
          id={id}
          type="number"
          step={property.type === "integer" ? 1 : "any"}
          min={property.minimum}
          max={property.maximum}
          value={value === null || value === undefined ? "" : String(value)}
          // Keep the raw text if it is not a number yet, so typing "1." works;
          // the server's validation reports anything still invalid on save.
          onChange={(event) => {
            const text = event.target.value;
            onChange(text === "" || Number.isNaN(Number(text)) ? text : Number(text));
          }}
        />
      );
    case "string":
      return (
        <input
          id={id}
          type={property.secret ? "password" : "text"}
          autoComplete={property.secret ? "new-password" : "off"}
          placeholder={property.examples?.length ? String(property.examples[0]) : undefined}
          value={String(value ?? "")}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    case "object":
      if (property.properties) {
        return (
          <NestedFields
            id={id}
            schema={schema}
            property={property}
            value={value}
            onChange={onChange}
          />
        );
      }
      return <JsonFieldInput id={id} value={value} onChange={onChange} />;
    default:
      return <JsonFieldInput id={id} value={value} onChange={onChange} />;
  }
}

/** A nested model's fields, each changing one key of the model's value. */
function NestedFields({ id, schema, property, value, onChange }: FieldInputProps) {
  const current = (value ?? {}) as Record<string, unknown>;
  return (
    <fieldset className="nested-fields" id={id}>
      {Object.entries(property.properties ?? {}).map(([key, rawChild]) => {
        const child = resolveProperty(schema, rawChild);
        const childId = `${id}-${key}`;
        return (
          <div className="field" key={key}>
            <label htmlFor={childId}>{child.title ?? key}</label>
            <FieldInput
              id={childId}
              schema={schema}
              property={child}
              value={current[key]}
              onChange={(childValue) => onChange({ ...current, [key]: childValue })}
            />
            {child.description && <div className="field-help">{child.description}</div>}
          </div>
        );
      })}
    </fieldset>
  );
}

function JsonFieldInput({ id, value, onChange }: Omit<FieldInputProps, "property" | "schema">) {
  // Text that is not JSON (yet) changes nothing; say so, since a backslash
  // in a Windows path ("D:\clips") silently kept the old value on the bench.
  const [invalid, setInvalid] = useState(false);
  return (
    <>
      <textarea
        id={id}
        rows={4}
        defaultValue={JSON.stringify(value, null, 2)}
        onChange={(event) => {
          try {
            onChange(JSON.parse(event.target.value));
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }}
      />
      {invalid && (
        <div className="field-error">
          Not valid JSON: this value is not changed (write \\ for a backslash).
        </div>
      )}
    </>
  );
}
