/**
 * Labelled inputs for the camera configuration forms, each showing the
 * server's validation message for its field (keyed by the field's path).
 */

import { useId, type ReactNode } from "react";

interface FieldProps {
  label: string;
  help?: string;
  error?: string;
  children: (id: string) => ReactNode;
}

export function Field({ label, help, error, children }: FieldProps) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {help && <div className="field-help">{help}</div>}
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  help?: string;
  error?: string;
  readOnly?: boolean;
  placeholder?: string;
}

export function TextField({ label, value, onChange, help, error, ...rest }: TextFieldProps) {
  return (
    <Field label={label} help={help} error={error}>
      {(id) => (
        <input
          id={id}
          value={value}
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
          {...rest}
        />
      )}
    </Field>
  );
}

interface NumberFieldProps {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  help?: string;
  error?: string;
  min?: number;
  max?: number;
  step?: number | "any";
}

/** A number input; empty means null (e.g. "no controller address"). */
export function NumberField({ label, value, onChange, help, error, ...limits }: NumberFieldProps) {
  return (
    <Field label={label} help={help} error={error}>
      {(id) => (
        <input
          id={id}
          type="number"
          value={value === null || Number.isNaN(value) ? "" : String(value)}
          onChange={(event) =>
            onChange(event.target.value === "" ? null : Number(event.target.value))
          }
          {...limits}
        />
      )}
    </Field>
  );
}

interface CheckFieldProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  error?: string;
}

export function CheckField({ label, checked, onChange, error }: CheckFieldProps) {
  return (
    <div className="field">
      <label>
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />{" "}
        {label}
      </label>
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

interface SelectFieldProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  help?: string;
  error?: string;
}

export function SelectField({ label, value, options, onChange, help, error }: SelectFieldProps) {
  // A saved value missing from the choices (an agent that is away) stays selectable.
  const choices = options.some((option) => option.value === value)
    ? options
    : [{ value, label: value ? `${value} (not available now)` : "—" }, ...options];
  return (
    <Field label={label} help={help} error={error}>
      {(id) => (
        <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          {choices.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
