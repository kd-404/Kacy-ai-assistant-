# Org Chart — Loom & Light (fictional demo company)

Four demo accounts, one per role, used throughout the eval suite and RBAC tests.

| Name | Email | Role | Reports to |
|---|---|---|---|
| Alice | alice@kacy-demo.test | Intern, Engineering | Bob |
| Bob | bob@kacy-demo.test | Software Engineer | Carla |
| Carla | carla@kacy-demo.test | Engineering Manager | Dev |
| Dev | dev@kacy-demo.test | CEO | — |

## Access rules this org chart implies

- An employee can see their own leave balance and salary, always.
- A manager can see their direct reports' leave balance and salary.
- An exec can see anyone's.
- Anyone else gets denied — this is the "intern asks for the CEO's salary" case the architecture is built around.
