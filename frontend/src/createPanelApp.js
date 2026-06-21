import { CoreMixin } from "./mixins/core.js";
import { PortsMixin } from "./mixins/ports.js";
import { CommerceMixin } from "./mixins/commerce.js";
import { DnsMixin } from "./mixins/dns.js";
import { AiDomainsMixin } from "./mixins/domains.js";

// Composes the admin panel from per-domain mixins. Vue merges each mixin's
// data()/computed/methods/watch, so `this.x` resolves across all of them — the
// split is purely organizational and behavior-identical to the former single
// options object. The in-DOM template still lives in templates/index.html.
export function createPanelApp(initialState) {
  return {
    mixins: [CoreMixin, PortsMixin, CommerceMixin, DnsMixin, AiDomainsMixin],

    mounted() {
      this.applyDashboard(initialState || {});
    },
  };
}
