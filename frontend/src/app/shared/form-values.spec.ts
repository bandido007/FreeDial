import { describe, it, expect } from "vitest";
import { Editor, Field } from "../common";
import { formValues } from "./form-values";
const field = (key: string, value: any, extra: Partial<Field> = {}): Field => ({
  name: key,
  key,
  kind: "text",
  label: key,
  value,
  errors: [],
  ...extra,
});
describe("Django form data contract", () => {
  it("omits immutable fields and preserves unchecked booleans, relation arrays and hidden inline identifiers", () => {
    const editor = {
      fields: [
        field("uid", "immutable", { readonly: true }),
        field("active", false),
        field("agents", ["1", "2"]),
      ],
      inlines: [
        {
          management: { "skills-TOTAL_FORMS": 1, "skills-INITIAL_FORMS": 1 },
          rows: [{ fields: [field("skills-0-id", "4", { hidden: true }), field("skills-0-DELETE", false)] }],
        },
      ],
    } as Editor;
    expect(formValues(editor)).toEqual({
      active: false,
      agents: ["1", "2"],
      "skills-TOTAL_FORMS": 1,
      "skills-INITIAL_FORMS": 1,
      "skills-0-id": "4",
      "skills-0-DELETE": false,
    });
  });
  it("splits date/time fields into the names Django validates, including clearing an existing value", () => {
    const editor = {
      fields: [
        field("expires_at", "2026-10-19T11:12:13", { split: true }),
        field("deleted_at", "", { split: true }),
      ],
      inlines: [],
    } as unknown as Editor;
    expect(formValues(editor)).toEqual({
      expires_at_0: "2026-10-19",
      expires_at_1: "11:12:13",
      deleted_at_0: "",
      deleted_at_1: "",
    });
  });
});
