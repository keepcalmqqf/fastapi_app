<script setup lang="ts">
import { listMembers } from '../api/member'
import type { Member } from '../api/member'
import { formatTime } from '../utils/datetime'

const loading = ref(false)
const members = ref<Member[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

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
  <div class="admin-page">
    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">会员管理</span>
          <div class="admin-toolbar__actions">
            <el-button @click="loadMembers">刷新</el-button>
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

      <div class="admin-pager">
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
/* 页面容器、工具栏与分页样式统一走 src/styles/admin.css */

.tip {
  margin-bottom: 16px;
}
</style>
