import { Editor, Field } from "../common";
/** Encode the existing Django form/inline contract; read-only values are never submitted. */
export function formValues(editor: Editor): Record<string, any> {
  const values: Record<string, any> = {};
  const add = (field: Field) => {
    if (field.readonly || field.kind === "readonly") return;
    if (field.split) {
      const [date, time] = (field.value || "").split("T");
      values[field.key + "_0"] = date || "";
      values[field.key + "_1"] = (time || "").slice(0, 8);
    } else values[field.key] = field.value ?? "";
  };
  for (const field of editor.fields) add(field);
  for (const inline of editor.inlines) {
    Object.assign(values, inline.management);
    for (const row of inline.rows) for (const field of row.fields) add(field);
  }
  return values;
}
