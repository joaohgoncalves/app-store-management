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


## 📄 License

MIT License — free for commercial and personal use.
