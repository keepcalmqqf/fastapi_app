<script setup lang="ts">
import { listMembers } from '../api/member'
import type { Member } from '../api/member'
import { formatTime } from '../utils/datetime'
import { translate as t } from '../plugins/i18n'

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
  } catch {
    // 同 UserManageView：onMounted / 分页 / 刷新按钮都不接管 Promise，
    // 错误提示已由响应拦截器弹出，这里必须吞掉 rejection
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
          <span class="admin-toolbar__title">{{ t('会员管理') }}</span>
          <div class="admin-toolbar__actions">
            <el-button @click="loadMembers">{{ t('刷新') }}</el-button>
          </div>
        </div>
      </template>

      <el-alert
        class="tip"
        type="info"
        :closable="false"
        :title="t('会员资料由会员本人自助维护，当前后端仅提供后台查看能力。')"
      />

      <el-table v-loading="loading" :data="members" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="nickname" :label="t('昵称')" min-width="120" />
        <el-table-column prop="email" :label="t('邮箱')" min-width="200" />
        <el-table-column :label="t('状态')" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? t('启用') : t('禁用') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('注册时间')" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="t('更新时间')" width="180">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <template #empty>{{ t('暂无会员') }}</template>
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
