<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { bootstrapAdmin } from '../api/auth'
import { useAuthStore } from '../stores/auth'
import { translate as t } from '../plugins/i18n'

const router = useRouter()
const authStore = useAuthStore()

// 本页只在「仍需初始化首个管理员」时可用；已初始化则回登录页。
// 这里不能用顶层 await：那会让 setup 返回 Promise，父组件树没有 <Suspense> 时组件不渲染。
// 同样的判断在 router/index.ts 的 beforeEach 里已经做过一次，此处只作组件级兜底
onMounted(async () => {
  if (authStore.needsBootstrap === null) await authStore.checkBootstrap()
  if (authStore.needsBootstrap === false) router.replace({ name: 'login' })
})

const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  name: '',
  email: '',
  password: '',
})

// 与后端校验规则保持一致：name 2-32 字符，password 8-64 位且同时包含字母和数字
const PASSWORD_PATTERN = /^(?=.*[A-Za-z])(?=.*\d).{8,64}$/

const rules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入昵称'), trigger: 'blur' },
    { min: 2, max: 32, message: t('长度需为 2-32 个字符'), trigger: 'blur' },
  ],
  email: [
    { required: true, message: t('请输入邮箱'), trigger: 'blur' },
    { type: 'email', message: t('邮箱格式不正确'), trigger: 'blur' },
  ],
  password: [
    { required: true, message: t('请输入密码'), trigger: 'blur' },
    {
      pattern: PASSWORD_PATTERN,
      message: t('密码需为 8-64 位，且同时包含字母和数字'),
      trigger: 'blur',
    },
  ],
}))

async function onSubmit() {
  if (!formRef.value) return
  // 同 LoginView：必须用 Promise 形态，Element Plus 不会 await 传给 validate 的
  // 回调，回调形态下这里只等到「校验结束」，注册请求还没发出去就 resolve 了，
  // 回调内的异常也无处可去
  try {
    await formRef.value.validate()
  } catch {
    // 校验未通过，错误已由表单内联展示
    return
  }
  loading.value = true
  try {
    await bootstrapAdmin(form.name, form.email, form.password)
    // 注册不种 cookie，继续调登录建立会话后直达首页
    await authStore.login(form.email, form.password)
    ElMessage.success(t('注册成功'))
    router.push('/')
  } catch {
    // 失败提示（如 401 注册已关闭 / 409 邮箱已存在）已由响应拦截器统一展示
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="register-page">
    <el-card class="register-card">
      <h2 class="title">{{ t('注册账号') }}</h2>
      <p class="hint">
        {{ t('当前尚无管理员账号，本页用于初始化首个管理员；初始化完成后入口自动关闭。') }}
      </p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item :label="t('昵称')" prop="name">
          <el-input v-model="form.name" maxlength="32" :placeholder="t('2-32 个字符')" clearable />
        </el-form-item>
        <el-form-item :label="t('邮箱')" prop="email">
          <el-input v-model="form.email" :placeholder="t('请输入邮箱')" clearable />
        </el-form-item>
        <el-form-item :label="t('密码')" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            :placeholder="t('8-64 位，须包含字母和数字')"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button class="submit-btn" type="primary" :loading="loading" @click="onSubmit">
          {{ t('注册') }}
        </el-button>
      </el-form>
      <div class="footer-link">
        {{ t('已有账号？') }}<router-link to="/login">{{ t('去登录') }}</router-link>
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
  background: var(--el-bg-color-page);
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
