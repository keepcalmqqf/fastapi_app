<script setup lang="ts">
import { listAuditLogs } from '../api/audit'
import type { AuditLog } from '../api/audit'
import { listMembers } from '../api/member'
import { listUsers } from '../api/user'
import { useAuthStore } from '../stores/auth'
import { formatTime } from '../utils/datetime'

const router = useRouter()
const authStore = useAuthStore()

// 最近一次成功刷新的时间（UTC ISO）
const lastUpdated = ref<string | null>(null)

// 统计值：加载中/失败统一展示为 '-'，避免用假数据占位
const stats = reactive<{ users: number | string; members: number | string; logs: number | string }>({
  users: '-',
  members: '-',
  logs: '-',
})

const recentLogs = ref<AuditLog[]>([])
const loading = ref(false)

const statCards = computed(() => [
  {
    key: 'users',
    label: '管理员用户',
    value: stats.users,
    icon: 'User',
    color: 'var(--el-color-primary)',
    route: 'users',
  },
  {
    key: 'members',
    label: '会员总数',
    value: stats.members,
    icon: 'Avatar',
    color: 'var(--el-color-success)',
    route: 'members',
  },
  {
    key: 'logs',
    label: '审计日志',
    value: stats.logs,
    icon: 'Document',
    color: 'var(--el-color-warning)',
    route: 'audit-logs',
  },
])

async function loadOverview() {
  loading.value = true
  try {
    // 各统计相互独立，任一失败不影响其余卡片展示
    const [usersRes, membersRes, logsRes] = await Promise.allSettled([
      listUsers(1, 1),
      listMembers(1, 1),
      listAuditLogs({ page: 1, page_size: 10 }),
    ])
    if (usersRes.status === 'fulfilled') stats.users = usersRes.value.data.total
    if (membersRes.status === 'fulfilled') stats.members = membersRes.value.data.total
    if (logsRes.status === 'fulfilled') {
      stats.logs = logsRes.value.data.total
      recentLogs.value = logsRes.value.data.items
    }
    lastUpdated.value = new Date().toISOString()
  } finally {
    loading.value = false
  }
}

// 概览每 60 秒自动刷新；离线或窗口失焦时暂停，避免后台标签页空转请求
const { active } = useConnectionStatus()
const { pause, resume } = useIntervalFn(loadOverview, 60_000)
watchEffect(() => (active.value ? resume() : pause()))

onMounted(loadOverview)

function actorLabel(row: AuditLog): string {
  const id = row.actor_id === null ? '-' : `#${row.actor_id}`
  return `${row.actor_type} ${id}`
}
</script>

<template>
  <div class="admin-page">
    <el-row :gutter="16">
      <el-col v-for="card in statCards" :key="card.key" :xs="24" :sm="12" :lg="8">
        <el-card
          class="stat-card stat-card--link"
          shadow="hover"
          @click="router.push({ name: card.route })"
        >
          <div class="stat-card__body">
            <el-icon class="stat-card__icon" :style="{ background: card.color }">
              <component :is="card.icon" />
            </el-icon>
            <div>
              <div class="stat-card__label">{{ card.label }}</div>
              <div class="stat-card__value">{{ card.value }}</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="12" :lg="8">
        <el-card class="stat-card" shadow="never">
          <div class="stat-card__body">
            <el-icon class="stat-card__icon" style="background: var(--el-text-color-secondary)">
              <UserFilled />
            </el-icon>
            <div>
              <div class="stat-card__label">当前账号</div>
              <div class="stat-card__value stat-card__value--sm">
                {{ authStore.user?.name ?? '-' }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">
            最近操作日志
            <span v-if="lastUpdated" class="admin-toolbar__time">
              更新于 {{ formatTime(lastUpdated) }}
            </span>
          </span>
          <div class="admin-toolbar__actions">
            <el-button @click="loadOverview">刷新</el-button>
            <el-button type="primary" plain @click="router.push({ name: 'audit-logs' })">
              查看全部
            </el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="recentLogs" border stripe>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="动作" min-width="160">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作者" width="120">
          <template #default="{ row }">{{ actorLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="操作对象" min-width="160">
          <template #default="{ row }">
            <span>{{ row.target_type }}</span>
            <span class="admin-muted"> · {{ row.target_id ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="来源 IP" width="140">
          <template #default="{ row }">{{ row.ip ?? '-' }}</template>
        </el-table-column>
        <template #empty>暂无审计日志</template>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.stat-card {
  margin-bottom: 16px;
}

/* 自动刷新的时间戳，弱化展示 */
.admin-toolbar__time {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.stat-card--link {
  cursor: pointer;
}

.stat-card__body {
  display: flex;
  align-items: center;
  gap: 14px;
}

.stat-card__icon {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  color: var(--el-color-white);
  font-size: 22px;
}

.stat-card__label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.stat-card__value {
  margin-top: 4px;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
}

.stat-card__value--sm {
  font-size: 18px;
}
</style>
