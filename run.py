# run.py
import webbrowser
import threading
import time
import sys
import os

def open_browser():
    """باز کردن مرورگر پس از راه‌اندازی سرور"""
    time.sleep(2)  # منتظر بمان تا سرور راه‌اندازی شود
    try:
        webbrowser.open('http://localhost:5000')
        print("مرورگر به طور خودکار باز شد...")
    except Exception as e:
        print(f"خطا در باز کردن مرورگر: {e}")
        print("لطفاً مرورگر را به صورت دستی باز کنید و به آدرس زیر بروید:")
        print("http://localhost:5000")

if __name__ == "__main__":
    # اجرای سرور در یک thread جداگانه
    from app21 import app
    
    # شروع thread برای باز کردن مرورگر
    browser_thread = threading.Thread(target=open_browser)
    browser_thread.daemon = True
    browser_thread.start()
    
    print("=" * 50)
    print("   برنامه سوئد یدک در حال راه‌اندازی...")
    print("=" * 50)
    print("اگر مرورگر به طور خودکار باز نشد، لطفاً:")
    print("1. مرورگر اینترنت خود را باز کنید")
    print("2. به آدرس زیر بروید:")
    print("   http://localhost:5000")
    print("=" * 50)
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        print(f"خطا در راه‌اندازی سرور: {e}")
        print("ممکن است پورت 5000 در حال استفاده باشد.")
        input("برای خروج Enter بزنید...")
