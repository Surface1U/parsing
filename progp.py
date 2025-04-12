USERNAME = "shavel"  # <-- Ваш логин
PASSWORD = "salo12321"  # <-- Ваш пароль

import re
import time
import requests
import tempfile
import os
import glob
import shutil
from io import StringIO
from bs4 import BeautifulSoup
import sqlite3


# pdfminer

from pdfminer.pdfpage import PDFPage
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfparser import PDFParser
from pdfminer.converter import TextConverter
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.layout import LAParams
from pdfminer.high_level import extract_text

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def save_to_sqlite(title: str, author: str, abstract: str, content: str, url: str, db_name='articles.db'):
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO articles (title, author, abstract, content, url)
            VALUES (?, ?, ?, ?, ?)
        ''', (title, author, abstract, content, url))
        conn.commit()
        conn.close()
        print(f"Сохранено в БД: {title}")
    except Exception as e:
        print(f"Ошибка при сохранении в БД: {e}")



def wait_for_captcha_to_be_solved(driver, timeout=300):
    """
    Ожидает, пока пользователь пройдет капчу вручную.
    Проверка выполняется по наличию элемента капчи.
    Таймаут по умолчанию — 5 минут.
    """
    print("Проверка наличия капчи...")

    try:
        # Попытка найти капчу (можно добавить другие условия по мере необходимости)
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//img[contains(@src, 'captcha')]"))
        )
        print("Обнаружена капча. Ожидаем, пока пользователь решит её вручную...")

        WebDriverWait(driver, timeout).until_not(
            EC.presence_of_element_located((By.XPATH, "//img[contains(@src, 'captcha')]"))
        )
        print("Капча решена. Продолжаем выполнение скрипта.")
    except Exception:
        print("Капча не обнаружена, продолжаем выполнение.")


def process_pdf(file_path):
    """
    Извлекает текст из PDF, считает общее число страниц,
    а также находит страницы, где меньше 100 символов.
    Возвращает:
       text (str) – весь текст
       pages_count (int) – общее количество страниц
       small_pages (list) – список кортежей (номер_страницы, кол-во_символов), где < 100 символов.
    """
    # Извлечение всего текста
    text = extract_text(file_path)
    # print(text)
    # Получение количества страниц
    with open(file_path, 'rb') as file:
        parser = PDFParser(file)
        doc = PDFDocument(parser)
        pages_count = len(list(PDFPage.create_pages(doc)))

        # Анализ каждой страницы отдельно
        rsrcmgr = PDFResourceManager(caching=True)
        laparams = LAParams()
        page_texts = []

        with StringIO() as output_string:
            device = TextConverter(rsrcmgr, output_string, laparams=laparams)
            interpreter = PDFPageInterpreter(rsrcmgr, device)

            for page_num, page in enumerate(PDFPage.create_pages(doc), 1):
                interpreter.process_page(page)
                current_text = output_string.getvalue()
                page_texts.append((page_num, len(current_text)))
                # Сбрасываем буфер, чтобы не накапливалось содержимое со всех страниц подряд
                output_string.truncate(0)
                output_string.seek(0)

    # Поиск страниц с менее чем 100 символами
    small_pages = [(num, count) for num, count in page_texts if count < 100]
    return text, pages_count, small_pages


def parse_article_info(article_soup):
    """
    Извлекает следующую информацию об статье:
      - Заголовок
      - Автор(ы)
      - Аннотация
      - (старый вариант ссылки на полный текст)
      - Источник (название журнала)
    """
    title = None
    authors = None
    annotation = None
    full_text_link = None
    source_link = None

    meta_title = article_soup.find("meta", property="og:title")
    if meta_title and meta_title.get("content"):
        title = meta_title.get("content").strip()
    else:
        p_title = article_soup.find("p", class_="bigtext")
        if p_title:
            title = p_title.get_text(strip=True)
        elif article_soup.title:
            title = article_soup.title.get_text(strip=True)

    meta_desc = article_soup.find("meta", property="og:description")
    if meta_desc and meta_desc.get("content"):
        desc = meta_desc.get("content")
        parts = desc.split('\n')
        if parts:
            authors = parts[0].strip()

    abstract_div = article_soup.find("div", id="abstract1")
    if abstract_div:
        annotation = abstract_div.get_text(" ", strip=True)

    ft_link_tag = article_soup.find("a", href=re.compile(r"javascript:(file_article|url_article)"))
    if ft_link_tag:
        full_text_link = ft_link_tag.get("href")

    source_tag = article_soup.find("a", title="Содержание выпусков этого журнала")
    if source_tag:
        source_link = source_tag.get_text(strip=True)

    return {
        "title": title,
        "authors": authors,
        "annotation": annotation,
        "full_text_link_old": full_text_link,  # старое значение, если понадобится
        "source_link": source_link
    }


def set_checkbox(driver, name, desired_state):
    """
    Устанавливает чекбокс с атрибутом name=<name> в состояние desired_state (True/False).
    """
    try:
        checkbox = driver.find_element(By.NAME, name)
        is_checked = checkbox.is_selected()
        if desired_state and not is_checked:
            checkbox.click()
        elif not desired_state and is_checked:
            checkbox.click()
    except Exception as e:
        print(f"Не найден чекбокс '{name}': {e}")


def init_db(db_name='articles.db'):
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            author TEXT,
            abstract TEXT,
            content TEXT,
            url TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()

def print_first_article(db_name='articles.db'):
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles ORDER BY id ASC LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        if result:
            print("\n--- Первый элемент в базе данных ---")
            print(f"ID: {result[0]}")
            print(f"Заголовок: {result[1]}")
            print(f"Автор: {result[2]}")
            print(f"Аннотация: {result[3][:200]}...")  # сокращаем длинные аннотации
            print(f"Контент: {result[4][:200]}...")    # сокращаем длинный текст
            print(f"URL: {result[5]}")
        else:
            print("База данных пуста.")
    except Exception as e:
        print(f"Ошибка при чтении из базы: {e}")



def main():
    # Создаём временную директорию для скачивания PDF
    download_dir = tempfile.mkdtemp()
    init_db()

    options = Options()
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Настраиваем автоматическое скачивание PDF без открытия в плагине
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-extensions")

    driver = webdriver.Chrome(options=options)

    # --- Авторизация ---
    driver.get("https://www.elibrary.ru/defaultx.asp")
    wait_for_captcha_to_be_solved(driver)

    try:
        login_input = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "login"))
        )
        login_input.clear()
        login_input.send_keys(USERNAME)

        password_input = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "password"))
        )
        password_input.clear()
        password_input.send_keys(PASSWORD)

        login_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'butred') and contains(text(), 'Вход')]"))
        )
        login_button.click()
        time.sleep(3)
        print("Авторизация прошла успешно.")
    except Exception as e:
        print("Ошибка авторизации:", e)
        driver.quit()
        shutil.rmtree(download_dir)
        return

    # --- Переход в расширенный поиск и задание параметров ---
    try:
        driver.get("https://www.elibrary.ru/querybox.asp")
        search_input = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.NAME, "ftext"))
        )
        search_input.clear()
        search_input.send_keys("генерация изображения")  # Ваш поисковый запрос

        # Где искать
        set_checkbox(driver, "where_name", True)       # в названии публикации
        set_checkbox(driver, "where_abstract", True)   # в аннотации
        set_checkbox(driver, "where_keywords", True)   # в ключевых словах
        set_checkbox(driver, "where_fulltext", False)  # в полном тексте – НЕ включаем
        set_checkbox(driver, "where_affiliation", False)

        # Тип публикации – включаем все типы
        for field in ["type_article", "type_disser", "type_book", "type_report",
                      "type_conf", "type_patent", "type_preprint", "type_grant", "type_dataset"]:
            set_checkbox(driver, field, True)

        # Параметры
        set_checkbox(driver, "search_morph", True)     # учитывать морфологию
        set_checkbox(driver, "search_freetext", False) # похожий текст – ВЫКЛ
        set_checkbox(driver, "search_fulltext", True)  # публикации с полным текстом
        set_checkbox(driver, "search_open", True)      # доступные для Вас
        set_checkbox(driver, "search_results", False)

        # Годы публикации: issues = "all"
        try:
            Select(driver.find_element(By.NAME, "issues")).select_by_value("all")
        except Exception as e:
            print("Не найден селект 'issues':", e)

        # Сортировка: по релевантности (rank) и по убыванию (rev)
        try:
            Select(driver.find_element(By.NAME, "orderby")).select_by_value("rank")
        except Exception as e:
            print("Не найден селект 'orderby':", e)
        try:
            Select(driver.find_element(By.NAME, "order")).select_by_value("rev")
        except Exception as e:
            print("Не найден селект 'order':", e)

        # Нажимаем кнопку "Поиск"
        search_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//td[@bgcolor='#F26C4F' and contains(@class, 'menus')]/a[@href='javascript:query_message()']"))
        )
        driver.execute_script("arguments[0].click();", search_button)
        print("Поиск запущен в режиме Расширенного поиска.")
        time.sleep(2)
        try:
            driver.switch_to.alert.accept()
        except:
            pass
    except Exception as e:
        print("Ошибка при настройке расширенного поиска:", e)
        driver.quit()
        shutil.rmtree(download_dir)
        return

    # --- Пагинация: собираем все идентификаторы статей ---
    all_article_ids = []
    while True:
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "restab")))
        except Exception as e:
            print("Ошибка ожидания таблицы результатов:", e)
            break

        time.sleep(2)
        results_html = driver.page_source
        soup = BeautifulSoup(results_html, "html.parser")
        result_table = soup.find("table", {"id": "restab"})
        if not result_table:
            print("Таблица с результатами не найдена.")
            break

        rows = result_table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) > 1:
                link_tag = cols[0].find("a")
                if link_tag:
                    js_link = link_tag.get("href", "")
                    match = re.search(r"javascript:(?:url_article|load_article)\((\d+)", js_link)
                    if match:
                        article_id = match.group(1)
                        if article_id not in all_article_ids:
                            all_article_ids.append(article_id)

        # Переход на «Следующая» (если есть)
        try:
            next_page = driver.find_element(By.LINK_TEXT, "Следующая")
            next_page.click()
            time.sleep(2)
        except Exception:
            break

    print(f"Найдено {len(all_article_ids)} статей со всех страниц.\n")

    articles_info = []
    original_window = driver.current_window_handle

    # --- Обработка каждой статьи ---
    for article_id in all_article_ids:
        article_url = f"https://www.elibrary.ru/item.asp?id={article_id}"
        driver.execute_script("window.open('');")
        driver.switch_to.window(driver.window_handles[-1])
        wait_for_captcha_to_be_solved(driver)
        try:
            driver.get(article_url)
            WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
            time.sleep(2)
            article_html = driver.page_source
            article_soup = BeautifulSoup(article_html, "html.parser")
            info = parse_article_info(article_soup)

            pdf_file_path = None
            pdf_text = ""
            try:
                # Находим ссылку "Полный текст"
                fulltext_link_tag = driver.find_element(By.XPATH,
                    "//a[starts-with(@href, 'javascript:file_article') and contains(text(), 'Полный текст')]"
                )
                if fulltext_link_tag:
                    # Перед кликом запоминаем список PDF-файлов в папке загрузок
                    current_files = set(glob.glob(os.path.join(download_dir, "*.pdf")))
                    fulltext_link_tag.click()

                    # Ожидаем появления нового PDF-файла (таймаут 20 сек)
                    timeout = 20
                    interval = 1
                    elapsed = 0
                    while elapsed < timeout:
                        new_files = set(glob.glob(os.path.join(download_dir, "*.pdf")))
                        diff = new_files - current_files
                        if diff:
                            pdf_file_path = diff.pop()
                            break
                        time.sleep(interval)
                        elapsed += interval

                    if pdf_file_path:
                        print(f"Скачан PDF-файл: {pdf_file_path}")
                        # Вместо старой get_pdf_text_from_file — вызываем process_pdf
                        text, pages_count, small_pages = process_pdf(pdf_file_path)
                        if pages_count > 0:
                            percentage = (len(small_pages) / pages_count) * 100
                            # Если у нас до 30% «пустых» страниц, считаем, что OK
                            if percentage <= 30:
                                pdf_text = text
                            else:
                                print(f"PDF для статьи {article_id}: Слишком много пустых страниц ({percentage:.1f}%).")
                                pdf_text = ""
                        else:
                            # PDF без страниц?
                            pdf_text = ""

                        # Удаляем скачанный файл — он нам больше не нужен
                        os.remove(pdf_file_path)
                    else:
                        print("PDF файл не найден в течение таймаута")
            except Exception as e:
                # print(f"PDF ссылка для статьи {article_id} не найдена или ошибка: {e}")
                pdf_text = ""

            info["pdf_text"] = pdf_text
            articles_info.append((article_id, info))

            # print(f"Статья ID={article_id}:")
            # print(f"  Заголовок: {info.get('title', '—')}")
            # print(f"  PDF: {'пропущен' if not pdf_text else 'извлечён текст'}")

            # Сохранение в excel
            save_to_sqlite(
                title=info.get('title', ''),
                author=info.get('authors', ''),
                abstract=info.get('annotation', ''),
                content=info.get('pdf_text', ''),
                url=f"https://www.elibrary.ru/item.asp?id={article_id}"
            )



        except Exception as e:
            print(f"Ошибка при загрузке статьи {article_id}: {e}")

        driver.close()
        driver.switch_to.window(original_window)

    driver.quit()
    shutil.rmtree(download_dir)

    print("\nСобранные статьи:")
    for aid, data in articles_info:
        title = data.get('title', '—')
        have_text = bool(data.get('pdf_text'))
        print(f"ID={aid}: {title} | Текст PDF {'ЕСТЬ' if have_text else 'НЕТ'}")


if __name__ == "__main__":
    main()
    print_first_article()
