<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { NButton, NConfigProvider, NDialogProvider, NDrawer, NDrawerContent, NMessageProvider } from "naive-ui";

import { naiveThemeOverrides } from "../shared/tokens.js";
import { logout, portal, refreshMe } from "./store.js";

const route = useRoute();
const themeOverrides = naiveThemeOverrides();
const isMobile = ref(false);
const mobileNavOpen = ref(false);

const links = [
  { to: "/", label: "Home" },
  { to: "/subscriptions", label: "My Service" },
  { to: "/orders", label: "Orders" },
  { to: "/plans", label: "Plans" },
];

const pageTitle = computed(() => {
  if (route.path.startsWith("/subscriptions")) return "My Service";
  if (route.path.startsWith("/orders")) return "Orders";
  if (route.path.startsWith("/plans")) return "Plans";
  return "Customer Home";
});

function updateIsMobile() {
  const mobile = typeof window !== "undefined" && window.innerWidth <= 840;
  isMobile.value = mobile;
  if (!mobile) mobileNavOpen.value = false;
}

onMounted(() => {
  updateIsMobile();
  if (typeof window !== "undefined") window.addEventListener("resize", updateIsMobile);
  if (!portal.me) refreshMe();
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") window.removeEventListener("resize", updateIsMobile);
});
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <div class="portal-shell">
          <aside class="portal-sidebar" aria-label="客户中心导航">
            <div class="portal-brand">
              <div class="portal-brand__mark" aria-hidden="true">XR</div>
              <div>
                <strong>Routing Panel</strong>
                <small>CUSTOMER PORTAL</small>
              </div>
            </div>
            <nav class="portal-nav">
              <RouterLink v-for="item in links" :key="item.to" :to="item.to" @click="mobileNavOpen = false">
                <span class="portal-nav__dot" aria-hidden="true"></span>
                <span>{{ item.label }}</span>
              </RouterLink>
            </nav>
            <div class="portal-sidebar__footer">
              <span>Signed in</span>
              <strong>{{ portal.me?.email || "Customer" }}</strong>
            </div>
          </aside>

          <div class="portal-main">
            <header class="portal-topbar">
              <div class="portal-topbar__title">
                <strong>{{ pageTitle }}</strong>
                <small>Subscription, usage and order management</small>
              </div>
              <div class="portal-action-row">
                <n-button v-if="isMobile" class="portal-mobile-nav" secondary size="small" @click="mobileNavOpen = true">Menu</n-button>
                <n-button size="small" tertiary @click="logout">退出登录</n-button>
              </div>
            </header>
            <main class="portal-content">
              <router-view />
            </main>
          </div>
        </div>

        <n-drawer v-if="isMobile" v-model:show="mobileNavOpen" :width="260" placement="left">
          <n-drawer-content title="Customer Portal" :native-scrollbar="false">
            <nav class="portal-nav" style="background:#11151b;padding:10px;border-radius:12px">
              <RouterLink v-for="item in links" :key="item.to" :to="item.to" @click="mobileNavOpen = false">
                <span class="portal-nav__dot" aria-hidden="true"></span>
                <span>{{ item.label }}</span>
              </RouterLink>
            </nav>
          </n-drawer-content>
        </n-drawer>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
