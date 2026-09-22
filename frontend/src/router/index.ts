import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
  },
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('../views/UserManageView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/members',
    name: 'members',
    component: () => import('../views/MemberManageView.vue'),
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局前置守卫：先恢复会话，未登录访问受保护页面跳转登录页；已登录访问登录/注册页跳回首页
router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (!authStore.restored) {
    await authStore.restore()
  }
  if (to.meta.requiresAuth && !authStore.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (authStore.user && (to.name === 'login' || to.name === 'register')) {
    return { name: 'home' }
  }
  return true
})

export default router
