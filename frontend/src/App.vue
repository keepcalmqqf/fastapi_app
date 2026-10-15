<script setup lang="ts">
// 标签页标题跟随路由，在根组件调用一次即可（useDocumentTitle 由 composables 目录自动导入）
useDocumentTitle()
</script>

<template>
  <router-view />
</template>
