from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime
import os
import random
import sys
import publish

def load_env(path='.env'):
    """Читаем KEY=VALUE из .env в os.environ (без внешних зависимостей)"""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

load_env()

class DTEKMonitor:
    def __init__(self):
        self.url = "https://www.dtek-kem.com.ua/ua/shutdowns"
        self.log_dir = 'logs'
        self.debug_dir = 'debug'
        self.successful_requests = 0
        self.failed_requests = 0
        self.driver = None
        self.first_load = True
        
        # Создаем папки
        for directory in [self.log_dir, self.debug_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
    
    def log_message(self, message, level="INFO"):
        """Запись сообщения в общий лог и вывод в консоль"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}"
        print(log_entry)
        
        log_file = os.path.join(self.log_dir, 'monitor.log')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')
    
    def save_debug_response(self, html_content, iteration):
        """Сохранение каждого ответа для отладки"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Определяем тип ответа
        if 'Incapsula' in html_content or 'incapsula' in html_content.lower():
            response_type = 'BLOCKED'
        elif '<!DOCTYPE html>' in html_content and ('ДТЕК' in html_content or 'dtek' in html_content.lower()):
            response_type = 'SUCCESS'
        else:
            response_type = 'UNKNOWN'
        
        debug_file = os.path.join(self.debug_dir, f'response_{iteration}_{timestamp}_{response_type}.html')
        
        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(f"<!-- Iteration: {iteration} -->\n")
            f.write(f"<!-- Timestamp: {timestamp} -->\n")
            f.write(f"<!-- Type: {response_type} -->\n")
            f.write(f"<!-- Content Length: {len(html_content)} bytes -->\n")
            f.write(html_content)
        
        return debug_file, response_type
    
    def save_successful_result(self, scripts_data, html_content):
        """Сохранение успешного результата в лог-файлы"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Сохраняем JSON
        json_file = os.path.join(self.log_dir, f'scripts_{timestamp}.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(scripts_data, f, ensure_ascii=False, indent=2)
        
        # Сохраняем HTML
        html_file = os.path.join(self.log_dir, f'page_{timestamp}.html')
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Сохраняем текстовый отчет
        txt_file = os.path.join(self.log_dir, f'scripts_{timestamp}.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(f"Успешный запрос: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Найдено скриптов: {len(scripts_data)}\n")
            f.write("="*80 + "\n\n")
            
            for script_info in scripts_data:
                f.write(f"--- SCRIPT {script_info['index']} ---\n")
                if script_info['src']:
                    f.write(f"SRC: {script_info['src']}\n")
                if script_info['type']:
                    f.write(f"TYPE: {script_info['type']}\n")
                if script_info['id']:
                    f.write(f"ID: {script_info['id']}\n")
                if script_info['content']:
                    content = script_info['content']
                    if len(content) > 500:
                        f.write(f"CONTENT (first 500 chars):\n{content[:500]}...\n")
                    else:
                        f.write(f"CONTENT:\n{content}\n")
                f.write("\n" + "="*80 + "\n\n")
        
        self.log_message(f"✓ Результаты сохранены: {json_file}", "SUCCESS")

        self.save_schedule(html_content, timestamp)

        # Обновляем последний успешный результат
        with open('dtek_scripts.json', 'w', encoding='utf-8') as f:
            json.dump(scripts_data, f, ensure_ascii=False, indent=2)
    
    def extract_schedule(self, html_content):
        """Достаём график отключений из inline-скрипта (DisconSchedule.fact = {...})"""
        marker = 'DisconSchedule.fact = '
        start = html_content.find(marker)
        if start == -1:
            return None
        try:
            fact, _ = json.JSONDecoder().raw_decode(html_content, start + len(marker))
            return fact
        except ValueError as e:
            self.log_message(f"Не удалось разобрать DisconSchedule.fact: {e}", "WARNING")
            return None

    def save_schedule(self, html_content, timestamp):
        """Сохранение графика отключений: в logs по времени и последний в schedule.json"""
        fact = self.extract_schedule(html_content)
        if not fact:
            self.log_message("График отключений (DisconSchedule.fact) не найден на странице", "WARNING")
            return

        with open(os.path.join(self.log_dir, f'schedule_{timestamp}.json'), 'w', encoding='utf-8') as f:
            json.dump(fact, f, ensure_ascii=False, indent=2)
        with open('schedule.json', 'w', encoding='utf-8') as f:
            json.dump(fact, f, ensure_ascii=False, indent=2)

        groups = sorted({g for day in fact.get('data', {}).values() for g in day})
        self.log_message(f"✓ График отключений: групп {len(groups)}, обновлено на сайте: {fact.get('update')}", "SUCCESS")
        
        publish.publish(fact, self.log_message)

    def init_driver(self):
        """Инициализация Chrome WebDriver с максимальной защитой от детекта"""
        try:
            chrome_options = Options()
            
            # КРИТИЧНО: НЕ используем headless режим
            chrome_options.add_argument('--headless=new')
            
            # Отключаем все признаки автоматизации
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # Настройки браузера
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--start-maximized')
            
            # User agent
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # Язык
            chrome_options.add_argument('--lang=uk-UA,uk;q=0.9')
            chrome_options.add_experimental_option('prefs', {
                'intl.accept_languages': 'uk-UA,uk,en-US,en'
            })
            
            # Отключаем уведомления и другие всплывающие окна
            chrome_options.add_argument('--disable-notifications')
            chrome_options.add_argument('--disable-popup-blocking')
            
            # Создаем драйвер
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # Устанавливаем таймауты
            self.driver.set_page_load_timeout(30)
            self.driver.implicitly_wait(10)
            
            # Скрываем признаки webdriver через CDP
            self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            
            # Удаляем все признаки автоматизации
            self.driver.execute_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['uk-UA', 'uk', 'en-US', 'en']
                });
                
                window.chrome = {
                    runtime: {}
                };
                
                Object.defineProperty(navigator, 'permissions', {
                    get: () => ({
                        query: () => Promise.resolve({ state: 'granted' })
                    })
                });
            """)
            
            self.log_message("✓ Chrome WebDriver инициализирован с антидетект настройками", "SUCCESS")
            return True
            
        except Exception as e:
            self.log_message(f"✗ Ошибка инициализации WebDriver: {str(e)}", "ERROR")
            return False
    
    def human_like_delay(self, min_seconds=1, max_seconds=3):
        """Человекоподобная задержка"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
    
    def simulate_human_behavior(self):
        """Имитация поведения человека"""
        try:
            # Скроллим страницу
            scroll_amount = random.randint(100, 500)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            self.human_like_delay(0.5, 1.5)
            
            # Двигаем мышку (через JavaScript)
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            self.driver.execute_script(f"""
                var event = new MouseEvent('mousemove', {{
                    'view': window,
                    'bubbles': true,
                    'cancelable': true,
                    'clientX': {x},
                    'clientY': {y}
                }});
                document.dispatchEvent(event);
            """)
            
        except Exception as e:
            self.log_message(f"Ошибка имитации поведения: {str(e)}", "DEBUG")
    
    def parse_once(self, iteration):
        """Одна попытка парсинга"""
        try:
            # При первой загрузке ждем дольше
            if self.first_load:
                self.log_message("Первая загрузка - ждем 10 секунд для прохождения проверки Incapsula...")
                self.driver.get(self.url)
                time.sleep(10)
                self.first_load = False
            else:
                # Обновляем страницу
                self.log_message("Обновление страницы...")
                self.driver.refresh()
                time.sleep(7)  # Увеличена задержка
            
            # Имитируем поведение человека
            self.simulate_human_behavior()
            
            self.log_message(f"Страница загружена: {self.driver.current_url}")
            
            # Дополнительная задержка
            self.human_like_delay(2, 4)
            
            # Получаем HTML
            html = self.driver.page_source
            
            # Сохраняем для отладки
            debug_file, response_type = self.save_debug_response(html, iteration)
            
            self.log_message(f"Размер ответа: {len(html)} байт")
            self.log_message(f"Тип ответа: {response_type}")
            
            # Дополнительная проверка в title
            try:
                title = self.driver.title
                self.log_message(f"Title страницы: {title}")
                
                if 'Incapsula' in title or 'Access Denied' in title:
                    response_type = 'BLOCKED'
            except:
                pass
            
            # Проверяем на блокировку
            if response_type == 'BLOCKED':
                self.log_message(f"⚠ ЗАБЛОКИРОВАНО Incapsula! Debug: {debug_file}", "WARNING")
                self.log_message("Попробуем подождать дольше при следующем запросе...", "WARNING")
                self.failed_requests += 1
                return False
            
            if response_type == 'SUCCESS':
                soup = BeautifulSoup(html, 'html.parser')
                scripts = soup.find_all('script')
                
                self.log_message(f"Найдено тегов <script>: {len(scripts)}")
                
                if scripts and len(scripts) > 0:
                    # Собираем данные о скриптах
                    results = []
                    for i, script in enumerate(scripts, 1):
                        content = script.string.strip() if script.string else ''
                        script_info = {
                            'index': i,
                            'src': script.get('src', ''),
                            'type': script.get('type', ''),
                            'id': script.get('id', ''),
                            'content': content,
                            'content_length': len(content)
                        }
                        results.append(script_info)
                        
                        # Краткая информация
                        if script.get('src'):
                            src_short = script.get('src')[:60]
                            self.log_message(f"  Script {i}: SRC={src_short}")
                        elif content and len(content) > 50:
                            self.log_message(f"  Script {i}: INLINE ({len(content)} chars)")
                    
                    # Сохраняем результаты
                    self.save_successful_result(results, html)
                    self.successful_requests += 1
                    self.log_message(f"✓ УСПЕШНО: {len(scripts)} скриптов сохранено", "SUCCESS")
                    return True
                else:
                    self.log_message(f"⚠ Скрипты не найдены", "WARNING")
                    self.failed_requests += 1
                    return False
            else:
                self.log_message(f"⚠ UNKNOWN тип ответа. Debug: {debug_file}", "WARNING")
                # Проверяем содержимое
                if 'shutdowns' in html.lower() or 'відключення' in html.lower():
                    self.log_message("Но найдены ключевые слова! Возможно, это успех...", "INFO")
                    # Пробуем парсить
                    soup = BeautifulSoup(html, 'html.parser')
                    scripts = soup.find_all('script')
                    if scripts:
                        self.log_message(f"Найдено {len(scripts)} скриптов!", "SUCCESS")
                        # Обрабатываем как успех
                        results = []
                        for i, script in enumerate(scripts, 1):
                            content = script.string.strip() if script.string else ''
                            script_info = {
                                'index': i,
                                'src': script.get('src', ''),
                                'type': script.get('type', ''),
                                'id': script.get('id', ''),
                                'content': content,
                                'content_length': len(content)
                            }
                            results.append(script_info)
                        
                        self.save_successful_result(results, html)
                        self.successful_requests += 1
                        return True
                
                self.failed_requests += 1
                return False
                
        except Exception as e:
            self.log_message(f"✗ Ошибка парсинга: {type(e).__name__}: {str(e)}", "ERROR")
            import traceback
            self.log_message(f"Traceback: {traceback.format_exc()}", "ERROR")
            self.failed_requests += 1
            return False
    
    def monitor_continuous(self):
        """Непрерывный мониторинг с интервалом 2 секунды"""
        self.log_message("="*80)
        self.log_message("ЗАПУСК МОНИТОРИНГА ДТЭК (Selenium + Антидетект)")
        self.log_message("Интервал запросов: 2 секунды")
        self.log_message("Используем Chrome с максимальной защитой от детекта")
        self.log_message("НЕ закрывайте окно браузера вручную!")
        self.log_message("Для остановки нажмите Ctrl+C")
        self.log_message("="*80)
        
        # Инициализируем браузер
        if not self.init_driver():
            self.log_message("Не удалось запустить браузер. Завершение работы.", "ERROR")
            return
        
        iteration = 0
        
        try:
            while True:
                iteration += 1
                self.log_message(f"\n{'='*60}")
                self.log_message(f"Попытка #{iteration}")
                self.log_message(f"{'='*60}")
                
                self.parse_once(iteration)
                
                # Статистика каждые 5 запросов
                if iteration % 5 == 0:
                    total = self.successful_requests + self.failed_requests
                    success_rate = (self.successful_requests / total * 100) if total > 0 else 0
                    self.log_message(f"\n📊 СТАТИСТИКА после {iteration} попыток:", "STATS")
                    self.log_message(f"   Успешных: {self.successful_requests} ({success_rate:.1f}%)", "STATS")
                    self.log_message(f"   Неудачных: {self.failed_requests}", "STATS")
                
                # Ждем перед следующим запросом
                wait_time = 2
                self.log_message(f"Ожидание {wait_time} секунд...")
                time.sleep(wait_time)
                
        except KeyboardInterrupt:
            self.log_message("\n" + "="*80)
            self.log_message("ОСТАНОВКА МОНИТОРИНГА", "INFO")
            self.log_message("="*80)
            self.log_message(f"Всего попыток: {iteration}")
            self.log_message(f"Успешных: {self.successful_requests}")
            self.log_message(f"Неудачных: {self.failed_requests}")
            if iteration > 0:
                success_rate = (self.successful_requests / iteration * 100)
                self.log_message(f"Процент успеха: {success_rate:.1f}%")
            self.log_message("="*80)
        finally:
            # Закрываем браузер
            if self.driver:
                try:
                    self.driver.quit()
                    self.log_message("Chrome WebDriver закрыт", "INFO")
                except:
                    pass

def run_once():
    """Один проход для запуска по расписанию (GitHub Actions): страница -> график -> рассылка -> выход"""
    monitor = DTEKMonitor()
    if not monitor.init_driver():
        return 1
    ok = False
    try:
        for attempt in range(1, 4):
            if monitor.parse_once(attempt):
                ok = True
                break
            time.sleep(5)
    finally:
        monitor.driver.quit()
    return 0 if ok else 1

def main():
    if '--once' in sys.argv:
        sys.exit(run_once())
    monitor = DTEKMonitor()
    monitor.monitor_continuous()

if __name__ == "__main__":
    main()