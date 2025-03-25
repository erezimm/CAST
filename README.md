# CAST: Candidate Alert System for Transients

**CAST** is a Django-based Target and Observation Manager (TOM) system designed to manage and follow up on astronomical transient candidates. It builds on the [TOM Toolkit](https://github.com/TOMToolkit/tom_base) and adds custom functionality such as real/bogus scoring, Transient Name Server (TNS) integration, and transient tracking utilities.

---

## 🚀 Installation

### 1. Install a Base TOM System

First, install a basic TOM system by following the instructions at:  
👉 https://github.com/TOMToolkit/tom_base

This sets up a Django-based TOM project that CAST builds upon.

---

### 2. Install the `candidates` App

Once your TOM base project is running, install the `candidates` application:

1. **Copy or clone the `candidates` app** into your TOM project directory.

2. **Add it to your Django `INSTALLED_APPS`** in `settings.py`:

   ```python
   INSTALLED_APPS = [
       # ... existing apps
       'candidates',
   ]
2. **Run database migrations**:
   ```python
   python manage.py makemigrations candidates
   python manage.py migrate

### 3. Update settings.py Configuration

Add the following to your settings.py:
   ```python
   # Transients settings
   TRANSIENT_DIR = '/home/erezz/marvin/transients/'  # Change this to your actual directory
