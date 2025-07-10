# CAST: Candidate Alert System for Transients

**CAST** is a Target and Observation Manager (TOM) system designed to manage and follow up on astronomical transient candidates from the Large Array Survey Telescope (LAST; [Ofek et al. 2023]([url](https://ui.adsabs.harvard.edu/abs/2023PASP..135f5001O/abstract))). It builds on the [TOM Toolkit](https://github.com/TOMToolkit/tom_base) and adds custom functionality such as a scanning page for the survey through the **Candidates** app.

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

   If you want to work with git, perform the following commands from your TOM directory:
   ```
   git init .
   git remote add origin git@github.com:erezimm/CAST.git
   git pull origin master 
   ```

3. **Add it to your Django `INSTALLED_APPS`** in `settings.py`:

   ```python
   INSTALLED_APPS = [
       # ... existing apps
       'candidates',
   ]
2. **Run database migrations**:
   ```python
   python manage.py makemigrations candidates
   python manage.py migrate

### 3. Update `settings.py` Configuration

1. **Add the path to the transient folder to your `settings.py`**:
   ```python
   # Transients settings
   TRANSIENT_DIR = '/path/to/transients/'  # Change this to your actual directory
   ```
2. **Add your Transient Name Server (TNS) credentials**:
   ```python
   BROKERS = {
     'TNS': {
        'api_key': '',
        'bot_id': '',
        'bot_name': '',
      ...

    },
   ```
   **NOTE**: For development, update the wis-tns urls to sandbox.wis-tns.org:
   
   In the file `candidates/models.py`, find and replace `www.wis-tns.org` with `sandbox.wis-tns.org` (2 occurences)

### 4. Update `urls.py` file
Make sure you have the `django` imports and the `about/` and `candidates/` paths in `urlpatterns`
   ```python
   from django.urls import path, include
   from django.views.generic import TemplateView

   urlpatterns = [
       path('', include('tom_common.urls')),
       path('about/', TemplateView.as_view(template_name='about.html'), name='about'),
       path('candidates/', include('candidates.urls')),  # Include URLs for the candidates app
   ]
   ```

### 5. Final Steps

1. **Collect static files:**
   ```python
   python manage.py collectstatic
   
3. **Run the development server:**
   ```python
   python manage.py runserver
   
Open http://127.0.0.1:8000 in your browser to use the application.
