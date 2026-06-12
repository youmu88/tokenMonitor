# Token Monitor 项目记忆

> 项目核心规范与约定，每次修订前请先查阅。

---

## 1. 版本号管理

采用 **3 位版本号**格式：`1.x.y`

| 位 | 名称 | 递增条件 | 示例 |
|---|------|---------|------|
| x | **次版本号** | 功能特性演进、新增组件、架构调整 | `1.2.0` |
| y | **补丁号** | 小问题修复、bugfix、微调优化 | `1.0.1` |

- 每次修订时，根据**变更规模**自动判断递增 x 还是 y
- 版本号记录在项目根目录的 `VERSION` 文件中
- 使用 `version_manager.py` 进行版本号自动管理

## 2. 构建检查

每次修订后**必须**通过构建检查，确保：

- ✅ 无语法错误（Python 语法检查）
- ✅ 无新增修订问题
- ✅ 模块导入正常
- ✅ 应用可正常启动

构建检查命令：
```bash
python3 -m py_compile token_status_app.py
python3 -c "import token_monitor_core; import token_db; import token_icon; import token_widget"
```

## 3. 自动归档

每次更新版本号后**自动归档**：

- **git commit**：自动生成简洁明了的 commit message
  - 格式：`✨ v{version} | {变更摘要}`
  - 示例：`✨ v1.2.0 | 🐍 token_monitor_core.py, token_status_app.py, ...`
- **git push**：自动推送到远程仓库

## 4. 版本号自动更新流程

```bash
# 小补丁修复（y 递增）
python3 version_manager.py --patch

# 功能特性演进（x 递增）
python3 version_manager.py --minor

# 仅检查当前版本状态
python3 version_manager.py --status
```

---

*最后更新：2026-06-12*