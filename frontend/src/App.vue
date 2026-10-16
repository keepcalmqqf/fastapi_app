<script setup lang="ts">
// 标签页标题跟随路由，在根组件调用一次即可（useDocumentTitle 由 composables 目录自动导入）
useDocumentTitle()

// 组件库语言包：ElementPlus 插件注册时只取一次 locale，动态切换语种要靠
// el-config-provider 下发，否则分页、空状态等内置文案不会跟着变
import { elementLocale } from './plugins/i18n/element'
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view />
  </el-config-provider>
</template>
