<script setup lang="ts">
// vue / vue-router 的 API 与 src/composables 下的项目 composable 均由自动导入提供
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { usePermissionStore } from '../stores/permission'
// i18n 插件：词条默认取本地文件，后端开启 i18n 插件后会用接口返回的译文覆盖
import {
  localeLabels,
  setLocale,
  translate,
  useI18n,
} from '../plugins/i18n'
import type { I18nLocale } from '../plugins/i18n'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

// 主题切换与网络状态
const { isDark, toggleDark } = useAppTheme()
const { online } = useConnectionStatus()

// 侧边栏折叠状态持久化，刷新后保持用户习惯
const collapsed = useStorage('admin.sidebar.collapsed', false)

// 侧边栏菜单直接由路由表生成，新增页面只需在 router 里加一条带 meta.title 的路由
// 同时按权限过滤：用户缺少某菜单所需权限时，菜单项自动隐藏
const permissionStore = usePermissionStore()

// 语言切换：写回 localStorage，组件库语言由 App.vue 的 el-config-provider 下发
const { locale: currentLocale, locales: supportedLocales } = useI18n()
function onLocaleChange(value: string | number) {
  setLocale(value as I18nLocale)
}

const menuItems = computed(() => {
  const root = router.options.routes.find((item) => item.path === '/')
  return (root?.children ?? []).filter((item) => {
    if (!item.meta?.title) return false
    if (item.meta.permission && !permissionStore.can(item.meta.permission)) return false
    return true
  })
})

// 面包屑：首页 + 当前命中的标题链路
const breadcrumbs = computed(() => route.matched.filter((item) => item.meta?.title).map((item) => item.meta!.title!))

// 头像占位文字：取昵称首字符，缺失时退回邮箱首字符
const initial = computed(() => {
  const name = authStore.user?.name?.trim()
  if (name) return name.slice(0, 1).toUpperCase()
  const email = authStore.user?.email?.trim()
  return email ? email.slice(0, 1).toUpperCase() : 'U'
})

async function onLogout() {
  try {
    await ElMessageBox.confirm(translate('确定退出登录吗？'), translate('退出确认'), {
      type: 'warning',
      confirmButtonText: translate('退出'),
      cancelButtonText: translate('取消'),
    })
  } catch {
    return
  }
  await authStore.logout()
  ElMessage.success(translate('已退出登录'))
  router.push({ name: 'login' })
}

function onCommand(command: string) {
  if (command === 'profile') router.push({ name: 'dashboard' })
  if (command === 'logout') onLogout()
}
</script>

<template>
  <el-container class="admin-layout">
    <el-aside class="admin-aside" :width="collapsed ? '64px' : '220px'">
      <div class="admin-brand">
        <span class="admin-brand__mark">FA</span>
        <span v-show="!collapsed" class="admin-brand__text">FastAPI 脚手架</span>
      </div>
      <el-menu
        class="admin-menu"
        router
        :default-active="route.path"
        :collapse="collapsed"
        :collapse-transition="false"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon>
            <component :is="item.meta?.icon ?? 'Menu'" />
          </el-icon>
          <template #title>{{ translate(item.meta?.title ?? '') }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <div class="admin-header__left">
          <el-button text :icon="collapsed ? 'Expand' : 'Fold'" @click="collapsed = !collapsed" />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ name: 'dashboard' }">{{
              translate('首页')
            }}</el-breadcrumb-item>
            <el-breadcrumb-item v-for="crumb in breadcrumbs" :key="crumb">
              {{ translate(crumb) }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="admin-header__right">
          <el-tag v-if="!online" type="danger" size="small" effect="dark">{{
            translate('网络已断开')
          }}</el-tag>

          <el-dropdown trigger="click" @command="onLocaleChange">
            <el-button text :icon="'Notebook'">
              {{ localeLabels[currentLocale] }}
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="item in supportedLocales"
                  :key="item"
                  :command="item"
                  :disabled="item === currentLocale"
                >
                  {{ localeLabels[item] }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-tooltip
            :content="isDark ? translate('切换为浅色') : translate('切换为深色')"
            placement="bottom"
          >
            <el-button text :icon="isDark ? 'Sunny' : 'Moon'" @click="toggleDark()" />
          </el-tooltip>

          <el-dropdown trigger="click" @command="onCommand">
            <span class="admin-user">
              <el-avatar :size="28" class="admin-user__avatar">{{ initial }}</el-avatar>
              <span class="admin-user__name">
                {{ authStore.user?.name ?? translate('未登录') }}
              </span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <div class="admin-user__email">{{ authStore.user?.email ?? '-' }}</div>
                <el-dropdown-item command="profile">{{ translate('个人资料') }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided>{{
                  translate('退出登录')
                }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="admin-main">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-layout {
  height: 100%;
}

.admin-aside {
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
  transition: width 0.2s ease;
  overflow: hidden;
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  padding: 0 18px;
  box-sizing: border-box;
  flex-shrink: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.admin-brand__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 6px;
  background: var(--el-color-primary);
  color: var(--el-color-white);
  font-size: 13px;
  font-weight: 700;
}

.admin-brand__text {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}

.admin-menu {
  flex: 1;
  border-right: none;
  overflow-x: hidden;
}

.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 20px;
  box-sizing: border-box;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.admin-header__left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-header__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.admin-user {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
  color: var(--el-text-color-primary);
}

.admin-user__avatar {
  background: var(--el-color-primary);
  color: var(--el-color-white);
  font-size: 13px;
}

.admin-user__name {
  font-size: 14px;
}

.admin-user__email {
  padding: 6px 16px 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.admin-main {
  padding: 0;
  height: calc(100% - 60px);
  overflow: hidden;
}
</style>
