<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()

const loading = ref(true)
const error = ref<string | null>(null)

async function loadMe() {
  loading.value = true
  error.value = null
  try {
    await authStore.fetchMe()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载用户信息失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadMe)
</script>

<template>
  <div class="home-page">
    <el-card class="home-card">
      <template #header>
        <div class="card-header">
          <span>FastAPI 全栈脚手架</span>
          <el-button type="danger" plain @click="authStore.logout">退出登录</el-button>
        </div>
      </template>
      <h3 class="welcome">欢迎，{{ authStore.user?.name ?? '用户' }}！</h3>
      <el-descriptions v-if="authStore.user" :column="1" border>
        <el-descriptions-item label="用户 ID">{{ authStore.user.id }}</el-descriptions-item>
        <el-descriptions-item label="昵称">{{ authStore.user.name }}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{ authStore.user.email }}</el-descriptions-item>
        <el-descriptions-item label="是否启用">
          <el-tag :type="authStore.user.is_active ? 'success' : 'danger'">
            {{ authStore.user.is_active ? '启用' : '禁用' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <el-result
        v-else-if="error"
        icon="error"
        title="加载失败"
        :sub-title="error"
      >
        <template #extra>
          <el-button type="primary" @click="loadMe">重试</el-button>
        </template>
      </el-result>
      <el-skeleton v-else :rows="4" animated />
      <p class="intro">
        本项目是一个基于 FastAPI + Vue 3 的生产级全栈脚手架，内置 JWT 认证、统一响应格式、
        全局异常处理与可插拔插件能力。
      </p>
    </el-card>
  </div>
</template>

<style scoped>
.home-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
}

.home-card {
  width: 560px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.welcome {
  margin: 0 0 16px;
}

.intro {
  margin: 16px 0 0;
  color: #909399;
  font-size: 13px;
  line-height: 1.6;
}
</style>
