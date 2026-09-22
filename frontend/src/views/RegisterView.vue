<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { register as apiRegister } from '../api/auth'

const router = useRouter()

const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  name: '',
  email: '',
  password: '',
})

// 与后端校验规则保持一致：name 2-10 字符，password 8-64 位且同时包含字母和数字
const PASSWORD_PATTERN = /^(?=.*[A-Za-z])(?=.*\d).{8,64}$/

const rules: FormRules = {
  name: [
    { required: true, message: '请输入昵称', trigger: 'blur' },
    { min: 2, max: 10, message: '长度需为 2-10 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    {
      pattern: PASSWORD_PATTERN,
      message: '密码需为 8-64 位，且同时包含字母和数字',
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await apiRegister(form.name, form.email, form.password)
      ElMessage.success('注册成功，请登录')
      router.push('/login')
    } catch {
      // 失败提示（如 401 注册已关闭 / 409 邮箱已存在）已由响应拦截器统一展示
    } finally {
      loading.value = false
    }
  })
}
</script>

<template>
  <div class="register-page">
    <el-card class="register-card">
      <h2 class="title">注册账号</h2>
      <p class="hint">首次部署时，注册用于初始化管理员账号；初始化完成后注册入口将关闭，请直接登录。</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item label="昵称" prop="name">
          <el-input v-model="form.name" placeholder="2-10 个字符" clearable />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" clearable />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="8-64 位，须包含字母和数字"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button class="submit-btn" type="primary" :loading="loading" @click="onSubmit">
          注册
        </el-button>
      </el-form>
      <div class="footer-link">
        已有账号？<router-link to="/login">去登录</router-link>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.register-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
}

.register-card {
  width: 400px;
}

.title {
  margin: 0 0 24px;
  text-align: center;
  font-size: 20px;
}

.hint {
  margin: -12px 0 20px;
  font-size: 12px;
  line-height: 1.6;
  color: #909399;
}

.submit-btn {
  width: 100%;
}

.footer-link {
  margin-top: 16px;
  text-align: center;
  font-size: 14px;
  color: #606266;
}
</style>
