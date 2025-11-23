# **Store Management System**  
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flet](https://img.shields.io/badge/Flet-026AA7?style=for-the-badge&logo=flet&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-07405E?style=for-the-badge&logo=sqlite&logoColor=white)
![Status](https://img.shields.io/badge/status-Active-success?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)

A robust **single-file retail management application**, designed for scalability and prepared for a future **clean, modular architecture**.  
Handles products, users, sales and reports with a modern UI and a structured internal workflow.

---

## 📑 **Table of Contents**
- [Overview](#overview)
- [Current Architecture](#current-architecture)
- [Features](#features)
- [Technologies](#technologies)
- [Getting Started](#getting-started)
- [Roadmap](#roadmap)
- [Planned Refactor](#planned-refactor)
- [Screenshots](#screenshots)
- [License](#license)

---

## 🧩 **Overview**

This project represents the **initial version** of a complete store management system built in a **single-file architecture**.  
Even though this version is monolithic, its structure is intentionally organized to support later modularization into `views/`, `services/`, `components/`, `db/` and `utils/`.

Ideal for:
- POS systems  
- Retail management  
- Inventory + sales tracking  
- Learning architecture patterns  
- MVP and prototyping  

---

## 🏗️ **Current Architecture**

```md
sistemalojinha.py
│
├── UI Components
├── Views (Login, Home, Users, Products, Sales, Reports)
├── State Management
├── SQLite Database Functions
├── Business Logic
└── Utilities & Helpers
```



> A complete modular refactor is planned for future releases.

---

## 🚀 **Features**

- 🔐 **User management** (roles + authentication)  
- 📦 **Product catalog** with inventory control  
- 🧾 **Sales processing** (multiple payment methods)  
- 📊 **Reports**: daily, monthly, product-level  
- 📝 **Activity logging** (audit trail)  
- 💾 **SQLite persistence layer**  
- 🎨 **Custom UI components** built with Flet  
- 🔄 **Auto-refresh** and consistent state flow  

---

## 🛠️ **Technologies**

- **Python 3**  
- **Flet**  
- **SQLite3**  
- **JSON**  
- **OS utilities**

---

## ⚙️ **Getting Started**

### 1. Install dependencies
```bash
pip install flet
```

2. Run the application

```bash
python sistemalojinha.py
```

## 🗺️ Roadmap

| Status | Feature |
|--------|---------|
| 🟢 | Initial single-file release |
| 🟡 | UI/UX improvements |
| 🟡 | Extract DB layer |
| 🟡 | Modularize components |
| 🔴 | Full clean architecture migration |
| 🔴 | Add tests (unit + integration) |
| 🔴 | Internationalization support |

---

## 📷 Screenshots

Aqui estão algumas telas principais do sistema:

<img src="https://github.com/user-attachments/assets/299cc0ac-9930-425c-87a5-d9ab7a2dd03d" alt="Login" width="400" />&nbsp;&nbsp;
<img src="https://github.com/user-attachments/assets/feb45f9d-1398-4255-8938-75a943d2a1dc" alt="Home" width="400" />

<br><br>

<img src="https://github.com/user-attachments/assets/8e543619-4781-4e2a-80c7-5aec53d80b8f" alt="User Management" width="400" />&nbsp;&nbsp;
<img src="https://github.com/user-attachments/assets/4f674bbc-03d5-4d14-8154-42b8e43dcd1b" alt="Product Management" width="400" />

<br><br>

<img src="https://github.com/user-attachments/assets/d553090f-5055-4d8d-81e2-55ca6d3de7e9" alt="Sales" width="400" />&nbsp;&nbsp;
<img src="https://github.com/user-attachments/assets/f7b069e5-6fb9-436a-b1ab-d9a1600d1907" alt="Reports" width="400" />


## 📄 License

MIT License — free for commercial and personal use.
