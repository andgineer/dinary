import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import TagPicker from "../src/components/TagPicker.vue";

const TAGS = [
  { id: 1, name: "vacation" },
  { id: 2, name: "work" },
  { id: 3, name: "groceries" },
];

describe("TagPicker", () => {
  it("renders a chip per tag", () => {
    const wrapper = mount(TagPicker, {
      props: { tags: TAGS, modelValue: [] },
    });
    expect(wrapper.findAll(".tag-chip")).toHaveLength(3);
    expect(wrapper.text()).toContain("vacation");
    expect(wrapper.text()).toContain("groceries");
  });

  it("marks chips checked when their id is in modelValue", () => {
    const wrapper = mount(TagPicker, {
      props: { tags: TAGS, modelValue: [1, 3] },
    });
    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    expect(checkboxes[0].element.checked).toBe(true);
    expect(checkboxes[1].element.checked).toBe(false);
    expect(checkboxes[2].element.checked).toBe(true);
  });

  it("emits update:modelValue with the toggled set on click", async () => {
    const wrapper = mount(TagPicker, {
      props: { tags: TAGS, modelValue: [1] },
    });
    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    await checkboxes[1].trigger("change");
    const emits = wrapper.emitted("update:modelValue");
    expect(emits).toBeTruthy();
    expect(emits[0][0].sort()).toEqual([1, 2]);
  });

  it("removes from selection when clicking a checked chip", async () => {
    const wrapper = mount(TagPicker, {
      props: { tags: TAGS, modelValue: [1, 2] },
    });
    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    await checkboxes[0].trigger("change");
    expect(wrapper.emitted("update:modelValue")[0][0]).toEqual([2]);
  });

  it("renders empty hint when there are no tags", () => {
    const wrapper = mount(TagPicker, {
      props: {
        tags: [],
        modelValue: [],
        emptyHint: "no tags yet — add one via + New",
      },
    });
    expect(wrapper.find(".tag-empty-hint").exists()).toBe(true);
    expect(wrapper.text()).toContain("no tags yet");
    expect(wrapper.find(".tags-list").exists()).toBe(false);
  });
});
