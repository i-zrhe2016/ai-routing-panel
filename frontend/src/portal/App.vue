<script setup>
import { h, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import {
  NButton,
  NConfigProvider,
  NDialogProvider,
  NDrawer,
  NDrawerContent,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NMessageProvider,
  NSpace,
} from "naive-ui";

import { naiveThemeOverrides } from "../shared/tokens.js";
import { logout, portal, refreshMe } from "./store.js";

const route = useRoute();
const themeOverrides = naiveThemeOverrides();

const isMobile = ref(false);
const mobileNavOpen = ref(false);
function updateIsMobile() {
  const mobile = typeof window !== "undefined" && window.innerWidth <= 768;
  isMobile.value = mobile;
  if (!mobile) mobileNavOpen.value = false;
}

const menuOptions = [
  { key: "/", label: () => h(RouterLink, { to: "/" }, { default: () => "总览" }) },
  { key: "/subscriptions", label: () => h(RouterLink, { to: "/subscriptions" }, { default: () => "我的订阅" }) },
  { key: "/orders", label: () => h(RouterLink, { to: "/orders" }, { default: () => "我的订单" }) },
  { key: "/plans", label: () => h(RouterLink, { to: "/plans" }, { default: () => "套餐" }) },
];

function activeKey() {
  if (route.path.startsWith("/subscriptions")) return "/subscriptions";
  if (route.path.startsWith("/orders")) return "/orders";
  if (route.path.startsWith("/plans")) return "/plans";
  return "/";
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
        <n-layout position="absolute">
          <n-layout-header bordered class="portal-header">
            <n-space align="center" :size="8" :wrap-item="false">
              <n-button v-if="isMobile" size="small" quaternary class="portal-nav-toggle" @click="mobileNavOpen = true">☰</n-button>
              <strong style="font-size:16px">订阅中心</strong>
            </n-space>
            <n-space align="center">
              <span v-if="portal.me && !isMobile" style="color:var(--c-text-muted)">{{ portal.me.email }}</span>
              <n-button size="small" tertiary @click="logout">退出登录</n-button>
            </n-space>
          </n-layout-header>
          <n-layout has-sider position="absolute" style="top:60px">
            <n-layout-sider v-if="!isMobile" bordered :width="200" content-style="padding:12px 0">
              <n-menu :value="activeKey()" :options="menuOptions" />
            </n-layout-sider>
            <n-layout-content :content-style="isMobile ? 'padding:16px;max-width:1080px' : 'padding:24px;max-width:1080px'">
              <router-view />
            </n-layout-content>
          </n-layout>
          <n-drawer v-if="isMobile" v-model:show="mobileNavOpen" :width="240" placement="left">
            <n-drawer-content :native-scrollbar="false" body-content-style="padding:12px 0">
              <n-menu :value="activeKey()" :options="menuOptions" @update:value="mobileNavOpen = false" />
            </n-drawer-content>
          </n-drawer>
        </n-layout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.portal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 24px;
  height: 60px;
}
.portal-nav-toggle {
  font-size: 18px;
}
@media (max-width: 768px) {
  .portal-header {
    padding: 0 16px;
  }
}
</style>
