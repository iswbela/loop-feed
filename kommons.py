from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

USERNAME = "seu_email@exemplo.com"
PASSWORD = "sua_senha"
NEW_DESCRIPTION = "Aqui vai a nova descrição do perfil."

service = Service(ChromeDriverManager().install())
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 15)

try:
    driver.get("https://kommons.com/login")
    time.sleep(2)

    try:
        cookie_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Accept')]")))
        cookie_button.click()
    except:
        pass

    wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='email']")))
    wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='password']")))

    driver.execute_script(f"document.querySelector('input[type=email]').value='{USERNAME}';")
    driver.execute_script(f"document.querySelector('input[type=password]').value='{PASSWORD}';")
    time.sleep(1)

    login_button = driver.find_element(By.XPATH, "//button[contains(text(),'Login') or contains(text(),'Sign In')]")
    driver.execute_script("arguments[0].click();", login_button)

    try:
        dashboard_element = wait.until(EC.presence_of_element_located((By.XPATH, "//h1[contains(text(),'Dashboard')]")))
    except:
        driver.save_screenshot("login_failed.png")
        raise Exception("Login não verificado")

    driver.get("https://kommons.com/members-area/ad/uk/CF8xwdAb414L0IqY")
    time.sleep(2)

    desc_box = wait.until(EC.presence_of_element_located((By.NAME, "description")))
    desc_box.clear()
    desc_box.send_keys(NEW_DESCRIPTION)

    save_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Save')]")))
    driver.execute_script("arguments[0].click();", save_button)

    wait.until(EC.text_to_be_present_in_element((By.XPATH, "//div[contains(@class,'alert')]"), "Profile updated"))

except Exception as e:
    driver.save_screenshot("error_debug.png")
    raise

finally:
    time.sleep(5)
    driver.quit()