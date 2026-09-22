import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { setupRequest } from './api/request'

const app = createApp(App)

setupRequest(router)

app.use(createPinia())
app.use(router)
app.use(ElementPlus)

app.mount('#app')
