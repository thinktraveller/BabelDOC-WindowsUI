# BabelDOC 工作台前端

React + TypeScript + Vite；生产环境的静态产物由后端挂载（`desktop/packaging/babeldoc.spec`
会把 `desktop/frontend/dist` 打进 `frontend_dist`）。

## 安装依赖

```powershell
cd D:\Done\BabelDOC\desktop\frontend
npm install
```

国内网络下载失败时改用镜像：

```powershell
npm install --registry https://registry.npmmirror.com
```

## 构建（供打包与后端挂载）

```powershell
npm run build      # 产物在 desktop\frontend\dist
npm test           # vitest 单元测试
```

后端在 `desktop\frontend\dist\index.html` 存在时会自动把它挂到 `/`，否则回退到内置的自检页面；
`/selfcheck` 始终可用。

## 开发模式（带热更新）

开发时 Vite 需要知道后端地址与会话令牌，二者都不写进仓库：

1. 启动后端并固定端口（仅开发用，产品默认随机端口）：

   ```powershell
   cd D:\Done\BabelDOC
   .\.venv\Scripts\python.exe -m babeldoc_workbench.main --no-window --port 8765
   ```

   终端会打印 `url=` 与 `token=`。

2. 复制 `desktop\frontend\.env.example` 为 `desktop\frontend\.env.local`，填入令牌：

   ```
   VITE_BACKEND_URL=http://127.0.0.1:8765
   VITE_WORKBENCH_TOKEN=<上一步打印的 token>
   ```

3. 启动前端开发服务器：

   ```powershell
   npm run dev
   ```

`.env.local` 已在 `.gitignore` 中；不要把真实令牌或 API Key 提交到仓库。
