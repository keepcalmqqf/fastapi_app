<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listMembers } from '../api/member'
import type { Member } from '../api/member'

const router = useRouter()

const loading = ref(false)
const members = ref<Member[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

function formatTime(value: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadMembers() {
  loading.value = true
  try {
    const res = await listMembers(page.value, pageSize.value)
    members.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadMembers()
}

function onSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadMembers()
}

onMounted(loadMembers)
</script>

<template>
  <div class="page">
    <el-card class="card">
      <template #header>
        <div class="card-header">
          <span>会员管理</span>
          <div>
            <el-button @click="loadMembers">刷新</el-button>
            <el-button @click="router.push('/')">返回首页</el-button>
          </div>
        </div>
      </template>

      <el-alert
        class="tip"
        type="info"
        :closable="false"
        title="会员资料由会员本人自助维护，当前后端仅提供后台查看能力。"
      />

      <el-table v-loading="loading" :data="members" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="nickname" label="昵称" min-width="120" />
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <template #empty>暂无会员</template>
      </el-table>

      <div class="pager">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
  background: #f5f7fa;
}

.card {
  max-width: 1080px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.tip {
  margin-bottom: 16px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
