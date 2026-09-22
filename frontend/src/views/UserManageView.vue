<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { listUsers, updateUser, deleteUser } from '../api/user'
import type { AdminUser } from '../api/user'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const users = ref<AdminUser[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

function formatTime(value: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadUsers() {
  loading.value = true
  try {
    const res = await listUsers(page.value, pageSize.value)
    users.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadUsers()
}

function onSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadUsers()
}

// 编辑对话框
const editVisible = ref(false)
const saving = ref(false)
const editFormRef = ref<FormInstance>()
const editingId = ref<number | null>(null)
const editForm = reactive({ name: '', is_active: true })

const editRules: FormRules = {
  name: [
    { required: true, message: '请输入昵称', trigger: 'blur' },
    { min: 2, max: 32, message: '昵称长度为 2-32 位', trigger: 'blur' },
  ],
}

function openEdit(row: AdminUser) {
  editingId.value = row.id
  editForm.name = row.name ?? ''
  editForm.is_active = row.is_active
  editVisible.value = true
}

async function onSave() {
  if (!editFormRef.value || editingId.value === null) return
  await editFormRef.value.validate()
  saving.value = true
  try {
    await updateUser(editingId.value, {
      name: editForm.name.trim(),
      is_active: editForm.is_active,
    })
    ElMessage.success('更新成功')
    editVisible.value = false
    loadUsers()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: AdminUser) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.name ?? row.email}」吗？该操作为软删除。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteUser(row.id)
  ElMessage.success('删除成功')
  // 删除当前页最后一条时回退一页，避免停在空页
  if (users.value.length === 1 && page.value > 1) {
    page.value -= 1
  }
  loadUsers()
}

onMounted(loadUsers)
</script>

<template>
  <div class="page">
    <el-card class="card">
      <template #header>
        <div class="card-header">
          <span>用户管理</span>
          <div>
            <el-button @click="loadUsers">刷新</el-button>
            <el-button @click="router.push('/')">返回首页</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="users" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="昵称" min-width="120" />
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="row.id === authStore.user?.id"
              @click="onDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>暂无用户</template>
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

    <el-dialog v-model="editVisible" title="编辑用户" width="420px">
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-width="70px">
        <el-form-item label="昵称" prop="name">
          <el-input v-model="editForm.name" maxlength="32" placeholder="2-32 位昵称" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="editForm.is_active"
            active-text="启用"
            inactive-text="禁用"
            inline-prompt
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
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

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
