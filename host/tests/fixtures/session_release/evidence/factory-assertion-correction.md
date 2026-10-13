# Уточнение обязательного доказательства фабрики

После успешного corrected-запуска координатор одобрил одну правку
независимого теста с прежними семью негативными потоками и тем же числом
тестов. Раньше assertRaises включал фабрику и последующий release; теперь
исключение обязано исходить непосредственно из фабрики. Это устраняет
возможность ложного успеха проверки за счёт отказа более позднего слоя.
Производственный код не менялся. Правка выполнена только после закрытия
corrected-запуска, его журналы и manifest сохранены неизменными.

До: 8e46885cfb40b63246830abeab70b189a6036ad976b12be6164a1c8259033066
После: 0ea0071b163a392865498daec425cd1bb555d7c080b9578a5fcbf0e89a3a0d8e

```diff
                 with self.assertRaises(SessionError):
-                    receipt = WindowReceipt.from_window(context, outcome)
-                    link.controller.release(receipt, 0)
+                    WindowReceipt.from_window(context, outcome)
```

Окончательное свидетельство: verified-manifest.json и verified-final-*.log.
