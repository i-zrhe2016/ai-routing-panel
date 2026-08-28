import { defineComponent, h, ref } from "vue";

// The admin bundle does not depend on vue-router, while the repository keeps
// portal source snapshots for a separate bundle. This lightweight test shim
// lets the shared Vitest suite exercise portal views without requiring the
// portal's production dependency during an offline admin build.
export const RouterLink = defineComponent({
  name: "RouterLink",
  props: { to: { type: [String, Object], default: "#" } },
  setup(props, { slots }) {
    return () => h("a", { href: typeof props.to === "string" ? props.to : "#" }, slots.default?.());
  },
});

export const RouterView = defineComponent({
  name: "RouterView",
  setup(_, { slots }) {
    return () => slots.default?.();
  },
});

export function useRoute() {
  return { path: ref("/") };
}

export function useRouter() {
  return { push() {} };
}

export function createWebHistory() {
  return {};
}

export function createRouter(options = {}) {
  return { ...options, push() {} };
}
