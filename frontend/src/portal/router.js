import { createRouter, createWebHistory } from "vue-router";

import OrderDetailView from "./views/OrderDetailView.vue";
import OrdersView from "./views/OrdersView.vue";
import OverviewView from "./views/OverviewView.vue";
import PlansView from "./views/PlansView.vue";
import SubscriptionDetailView from "./views/SubscriptionDetailView.vue";
import SubscriptionsView from "./views/SubscriptionsView.vue";

export function createPortalRouter() {
  return createRouter({
    history: createWebHistory("/portal"),
    routes: [
      { path: "/", name: "overview", component: OverviewView },
      { path: "/subscriptions", name: "subscriptions", component: SubscriptionsView },
      { path: "/subscriptions/:id", name: "subscription-detail", component: SubscriptionDetailView, props: true },
      { path: "/orders", name: "orders", component: OrdersView },
      { path: "/orders/:orderNo", name: "order-detail", component: OrderDetailView, props: true },
      { path: "/plans", name: "plans", component: PlansView },
    ],
  });
}
