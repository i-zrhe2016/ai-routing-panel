<script setup>
import { h, onMounted } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import {
  NButton,
  NConfigProvider,
  NDialogProvider,
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
  if (!portal.me) refreshMe();
});
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <n-layout position="absolute">
          <n-layout-header
            bordered
            style="display:flex;align-items:center;justify-content:space-between;padding:0 24px;height:60px"
          >
            <strong style="font-size:16px">订阅中心</strong>
            <n-space align="center">
              <span v-if="portal.me" style="color:var(--c-text-muted)">{{ portal.me.email }}</span>
              <n-button size="small" tertiary @click="logout">退出登录</n-button>
            </n-space>
          </n-layout-header>
          <n-layout has-sider position="absolute" style="top:60px">
            <n-layout-sider bordered :width="200" content-style="padding:12px 0">
              <n-menu :value="activeKey()" :options="menuOptions" />
            </n-layout-sider>
            <n-layout-content content-style="padding:24px;max-width:1080px">
              <router-view />
            </n-layout-content>
          </n-layout>
        </n-layout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
