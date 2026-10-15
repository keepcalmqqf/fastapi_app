<script setup lang="ts">
import { listAuditLogs } from '../api/audit'
import type { AuditLog, AuditLogParams } from '../api/audit'
import { formatTime, toUtcIso } from '../utils/datetime'

const loading = ref(false)
const logs = ref<AuditLog[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 筛选条件
const action = ref('')
const actorId = ref<number | null>(null)
const timeRange = ref<[Date, Date] | null>(null)

// 常用动作预设，需与 app/services/audit.py 的 ACTION_* 常量保持一致；
// allow-create 允许输入预设之外的新动作
const actionOptions = [
  'user.create',
  'user.update',
  'user.delete',
  'user.change_password',
  'member.delete',
  'member.change_password',
  'auth.login_failed',
]

type TagType = 'success' | 'info' | 'warning' | 'danger'

function actionTagType(value: string): TagType {
  if (value.includes('login_failed') || value.includes('delete')) return 'danger'
  if (value.includes('update') || value.includes('change_password')) return 'warning'
  if (value.includes('create')) return 'success'
  return 'info'
}

function formatDetail(detail: string | null): string {
  if (!detail) return '无'
  try {
    return JSON.stringify(JSON.parse(detail), null, 2)
  } catch {
    return detail
  }
}

function actorLabel(row: AuditLog): string {
  const id = row.actor_id === null ? '-' : `#${row.actor_id}`
  return `${row.actor_type} ${id}`
}

async function loadLogs() {
  loading.value = true
  try {
    const params: AuditLogParams = {
      page: page.value,
      page_size: pageSize.value,
      action: action.value.trim() || undefined,
      actor_id: actorId.value ?? undefined,
      created_at_from: toUtcIso(timeRange.value?.[0]),
      created_at_to: toUtcIso(timeRange.value?.[1]),
    }
    const res = await listAuditLogs(params)
    logs.value = res.data.items
    total.value = res.data.total
  } catch {
    // 失败原因已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

// 筛选条件序列化成 key 后再 watch：el-input-number / el-select 在 input 与 change 时
// 各会改一次值，直接 watch 三个 ref 会对同一次操作发两遍相同请求；
// 改成 watch 派生 key，只有条件真正变化时才触发一次。
// 时间范围也走 400ms 防抖，避免边调日期边连续请求。
const filterKey = computed(() =>
  JSON.stringify([
    action.value.trim(),
    actorId.value ?? null,
    timeRange.value?.map((item) => item?.toISOString()) ?? null,
  ]),
)

watchDebounced(filterKey, onSearch, { debounce: 400 })

function onSearch() {
  page.value = 1
  loadLogs()
}

function onReset() {
  const hadFilters = action.value !== '' || actorId.value !== null || timeRange.value !== null
  action.value = ''
  actorId.value = null
  timeRange.value = null
  // 条件本就是空的，watch 不会触发，这里显式回到第一页刷新
  if (!hadFilters) {
    page.value = 1
    loadLogs()
  }
}

function onPageChange(p: number) {
  page.value = p
  loadLogs()
}

function onSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadLogs()
}

// 详情抽屉
const detailVisible = ref(false)
const detailRow = ref<AuditLog | null>(null)

function openDetail(row: AuditLog) {
  detailRow.value = row
  detailVisible.value = true
}

onMounted(loadLogs)
</script>

<template>
  <div class="admin-page">
    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">审计日志</span>
          <div class="admin-toolbar__actions">
            <el-button @click="loadLogs">刷新</el-button>
          </div>
        </div>
      </template>

      <el-form class="filters" :inline="true" @submit.prevent="onSearch">
        <el-form-item label="动作">
          <el-select
            v-model="action"
            filterable
            clearable
            allow-create
            default-first-option
            placeholder="全部动作"
            style="width: 220px"
          >
            <el-option v-for="item in actionOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="操作者 ID">
          <el-input-number v-model="actorId" :min="1" :controls="false" placeholder="不限" />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="logs" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="动作" width="180">
          <template #default="{ row }">
            <el-tag :type="actionTagType(row.action)" size="small">{{ row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作者" width="130">
          <template #default="{ row }">{{ actorLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="操作对象" min-width="170">
          <template #default="{ row }">
            <span>{{ row.target_type }}</span>
            <span class="admin-muted"> · {{ row.target_id ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="来源 IP" width="140">
          <template #default="{ row }">{{ row.ip ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="!row.detail" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>暂无审计日志</template>
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

    <el-drawer v-model="detailVisible" title="日志详情" size="420px">
      <el-descriptions v-if="detailRow" :column="1" border>
        <el-descriptions-item label="日志 ID">{{ detailRow.id }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ formatTime(detailRow.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="动作">{{ detailRow.action }}</el-descriptions-item>
        <el-descriptions-item label="操作者">{{ actorLabel(detailRow) }}</el-descriptions-item>
        <el-descriptions-item label="操作对象">
          {{ detailRow.target_type }} · {{ detailRow.target_id ?? '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="来源 IP">{{ detailRow.ip ?? '-' }}</el-descriptions-item>
      </el-descriptions>
      <h4 class="detail-title">变更要点</h4>
      <pre class="detail-body">{{ formatDetail(detailRow?.detail ?? null) }}</pre>
    </el-drawer>
  </div>
</template>

<style scoped>
/* 页面容器、工具栏与分页样式统一走 src/styles/admin.css */

.filters {
  margin-bottom: 8px;
}

.detail-title {
  margin: 20px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.detail-body {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
