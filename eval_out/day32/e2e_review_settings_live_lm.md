# E2E: Ревью Pull Request, настройки

Изолированная копия на портах 18000/18001; приложение на 8000/8001 не затрагивалось.

| Шаг | Статус | Подробности |
|-----|--------|-------------|
| 1 section visible, status 'Не построен', refresh disabled with hint | passed | 'Ревью Pull Request', 'Не построен', disabled=True, hint=True |
| 2 provider is LM Studio, cloud note hidden | passed | selected='lm_studio/LM Studio', note_hidden=True |
| 3 empty path shows validation text and sends no PUT | passed | 'Укажите путь к репозиторию.', puts=0 |
| 4 folder that is not a git repository shows an error and keeps the value | passed | 'Не удалось прочитать репозиторий: Папка не является git-репозиторием: C:\\Users\\Aleksey\\AppData\\Local\\Temp\\review_e2e_2a4ng8hs\\not_a_repo. Проверьте путь и повторите.', kept=True |
| 5 git repository saved, path survives reopening, refresh enabled | passed | path_kept=True, got='C:/Users/Aleksey/AppData/Local/Temp/review_e2e_2a4ng8hs/gitrepo/work', want='C:\\Users\\Aleksey\\AppData\\Local\\Temp\\review_e2e_2a4ng8hs\\gitrepo\\work', refresh_enabled=True |
| 6a refresh success branch | passed | Обновлён: 17.10.2026, 03:15:25, файлов: 2 |
| 6b refresh error branch shows the LM Studio text | не запускалось (embeddings were reachable) |  |
| 7 cloud provider shows the cloud note | passed | note_visible=True |
