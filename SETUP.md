# VERISAFE Setup Guide

Follow these steps to set up your VERISAFE development environment on Windows:

## 1. Clone the Repository

```
git clone https://github.com/requisimus-IT-Consultancy-Pvt-Ltd/VERISAFE-Framework.git
cd VERISAFE-Framework
```

## 2. Run the Setup Script

Run the following command in the VERISAFE-Framework directory:

```
install.bat
```

This will:
- Check for Python and create a `.venv` virtual environment if needed
- Activate the environment
- Upgrade pip
- Install all required Python dependencies for the framework and submodules

## 3. Set Up Gemini API Key

1. Visit: https://aistudio.google.com/api-keys
2. Generate or copy your Gemini API key.
3. In your terminal, set the environment variable (replace `your-key-here`):

```
set GEMINI_API_KEY=AIzaSyCG6oE9-Vsl8GVCVg28W3lFX6epMn5U8G8
```

To verify, run:

```
echo %GEMINI_API_KEY%
```

## 4. Vendor GoogleTest (for C/C++ test generation)

Open PowerShell in the VERISAFE-Framework directory and run:

```
./vendor_googletest.ps1
```

This will download and set up the GoogleTest framework required for C/C++ unit test generation.

---

**You are now ready to use VERISAFE!**

Refer to the README for usage instructions.
