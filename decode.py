import os,re, subprocess, time, shutil
import xml.etree.cElementTree as ET  # подключаем библиотеку XML
from multiprocessing import Pool # для многопоточной раскладки
from itertools import repeat
from ExtractProc_3  import extract_one # найдено в GComp и адаптировано под python 3.5
import glob
import argparse # работа с командной строкой

onectypes={} # словарь словарей. На первом уровне - типы элементов (главное окно, кнопка, ...) На втором - порядковые номера элементов и их имена
profiler_results = {}

settings = {'extract_included_epf': True, # если есть макеты, в которых находятся epf, то они будут тоже разобраны
            'split_module_text' : True} # разбивать тексты модуле на отдельные файлы (методы)


# region Commons
def indent(elem, level=0):
    """готовит ElementTree для pretty print"""
    i = "\n" + level * "\t"
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "\t"
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

class Profiler(object):
    """класс для замеров времени выполнения различных функций / строк кода"""
    funcname=''
    profiler_results={}
    def __init__(self,fn='',pr={}):
        self.funcname = fn
        self.profiler_results = pr
    def __enter__(self):
        self._startTime = time.time()

    def __exit__(self, type, value, traceback):
        l_time = (time.time() - self._startTime)

        if self.funcname in self.profiler_results:
            self.profiler_results[self.funcname] = self.profiler_results[self.funcname] + l_time
        else:
            self.profiler_results[self.funcname] = l_time

    def print_results(results):
        """выведем результаты красиво"""
        for el in results: # results - не локальные!
            print(str(results[el])[:5] , '\t: ', el)
    #print("Elapsed time: {:.3f} sec".format(l_time))

def getxmlbyindexes(root,indexes):
    """
    Возвращает элемент по последовательности индексов. Это упрощенный аналог xpath
    """
    query='.'
    for i in indexes:
        query=query+'/elem['+str(i+1)+']'
    return root.findall(query)[0]

def _localExec(command):
    """Просто выполняет команду системы"""
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
    return proc.stdout.read()
    # end def _localExec()

# endregion


class ones_object(object):
    """класс для хранения объекта 1С, полученного из файла на диске"""
    filename = None
    ones_type = None
    replaces = None
    object_as_list = None

    def __enter__(self):
        return self
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __init__(self, filename='', ones_type='' ):
        self.ones_type = ones_type
        self.filename = filename
        self.replaces = ReplacesForForms()
        # и сразу прочитаем
        self.object_as_list = self.filetolist()

    def original_value(self,cur):
        # вернет исходное значение из словаря замен

        if type(cur) == str:
            if cur.startswith('text'):
                return self.replaces.textreplaces['"'+cur+'"']
            elif cur.startswith('base64'):
                return self.replaces.base64replaces['"' + cur + '"']
            else:
                return cur.replace('_','-') # guid
        else:
            return cur

    def value_by_address(self, address):
        #path = '3-4-5'
        cur = self.object_as_list
        for i in address.split('-'):
            cur = cur[int(i)]

        return self.original_value(cur)


    def serialize(self, outfilename=''):
        """
        Превращает файл обычной формы в XML
        """
        if outfilename == '':
            outfilename = self.filename + '.xml' # по умолчанию будем писать тут же рядом

        xmlroot = ET.Element("root")  # рутовый элемент
        self.list_to_ET(self.object_as_list, xmlroot, 1)

        self.givenames(xmlroot, self.ones_type) # подпишем полученные теги

        with Profiler('ET.tostring', profiler_results) as p:
            indent(xmlroot)
            message = ET.tostring(xmlroot, "utf-8")
            open(outfilename, 'wb').write(message)
            # а писать лучше уже в какой-то другой файл

    def list_to_ET(self, elements, xmlelement, level=0):
        """
        Собирает XML из массива. Все элементы имеют имя 'elem' и порядковый номер 'order'
        """
        i = -1

        for element in elements:
            i += 1
            linexml = ET.SubElement(xmlelement, "elem")
            linexml.set('order', str(i))  # добавляет порядковый номер элемента в атрибуты

            if type(element) is list:
                self.list_to_ET(element, linexml, level + 1)
            else:
                linexml.text = str(self.original_value(element))
            #
            # elif type(element) is str:
            #     if element.startswith(
            #             'text'):  # если текущий элемент - текст, начинающийся на "text", значит, исходное значение лежит в словаре
            #         linexml.text = self.replaces.textreplaces['"' + element + '"']
            #     else:
            #         linexml.text = element.replace('_', '-')  # а это уже исковерканные гуиды, вернем их обратно
            # else:
            #     linexml.text = str(element)

    def filetolist(self):
        """
        Преобразует текст обычной формы в массив массивов массивов (вроде дерева)
        """

        # region RexExpFunctions
        def textrepl(match):
            """
            Вспомогательная функция
            Заменяет тексты в кавычках на "text0".."text123", а исходные значения складывает в словарь replaces
            Потом восстанавливает обратно в момент создания xml
            """
            replacenumberastext = '"text' + str(self.replaces.replacenumber) + '"'
            self.replaces.textreplaces[replacenumberastext] = match.group()
            self.replaces.replacenumber += 1
            return replacenumberastext

        def base64repl(match):
            """
            Вспомогательная функция
            Заменяет base64 на "base64_0".."base64_123", а исходные значения складывает в словарь base64_replaces
            Потом восстанавливает обратно в момент создания xml
            """
            replacenumberastext = '"base64_' + str(
                self.replaces.replacenumber) + '"'  # пользуемся одним и тем же replacenumber, но в данном случае не критично
            self.replaces.base64replaces[replacenumberastext] = match.group()
            self.replaces.replacenumber += 1
            return replacenumberastext

        guidrepl = lambda match: '"' + match.group().replace('-', '_') + '"'

        # endregion

        with open(self.filename, 'r', encoding='utf-8') as file:
            text = file.read()[1:] #.encode('utf-8').decode('utf-8') # Первые 3 байта приходятся на BOM, они не нужны.

        text_start = text # бэкап для отладки

        # текст между 2 группами нечетного числа кавычек закинем в словарь
        # pattern = re.compile(r'(?<!["])(")("")*?(?!["]).*?[^"](")("")*?(?!["])',flags=re.DOTALL)
        pattern = re.compile(r'(?<!["])' # Перед фрагментом должен стоять любой символ, кроме кавычки. Этот символ не включается во фрагмент
                             r'(")("")*?' # дальше идет нечетное число кавычек
                             r'(?!["])' #которое зананчивается любым символом кроме кавычки
                             r'.*?' # затем - любое число любых символов(включая 0), ленивая квантификация (все равно не запомню и полезу на вики)
                             r'[^"]' # что угодно, кроме кавычки, 1 штука
                             r'(")("")*?' #снова нечетное число кавычек
                             r'(?!["])' # и затем - любой символ кроме кавычки. Этот символ не включается во фрагмент.
                             ,flags=re.DOTALL)
        text = pattern.sub(textrepl, text)

        # преобразуем гуиды к строкам, чтобы их можно было закинуть в массив
        # функция замены простая, а потому будет лямбдой
        pattern = re.compile(r'(\w{8}-\w{4}-\w{4}-\w{4}-\w{12})')
        text = pattern.sub(guidrepl, text)

        # теперь надо позаботиться о base64
        pattern = re.compile(r'\{#base64:' # начало {#base64:
                             r'.{1,}?' # любое количество других символов
                             r'\}' # и закончится на }
                             ,flags=re.DOTALL)
        text = pattern.sub(base64repl, text) # преобразуем гуиды к строкам, чтобы их можно было закинуть в массив

        # Далее 2 временных патча некоторых особенностей в формах.
        # Обратное преобразование невозможно, пока не придумаю, что делать с этими костылями.
        # Порядок их выполнения важен

        binaryrepl = lambda match: '"' + match.group() + '"'

        # 1) В обычных формах встречается такое: 31.00000000000002 или 1.6e2
        # тоже обернем в кавычки
        # pattern = re.compile(r'\d+\.\d+')
        pattern = re.compile(r'\d+' # одна или несколько цифр
                             r'\.' # точка
                             r'(\d|a|b|c|d|e|f)+') # один или несколько символов: 0..9 или a..f (т.е. hex)
        text = pattern.sub(binaryrepl, text)

        # 2) в управляемых формах появились такие конструкции: 00010101000000. Т.е. нули-единицы, не текст. Просто обернем их в кавычки
        pattern = re.compile(r'(?<![.])' # начинается НЕ с точки
                             r'\d{14}') # и содержит росно 14 цифр. Кажется, встречались не только 0-1
        text = pattern.sub(binaryrepl, text)

        text = text.replace('}', ']').replace('{', '[') # теперь текст можно преобразовать так, чтобы Питон увидел в нем массив массивов массивов
        return eval(text) # и получим наконец этот массив.

    def givenames(self, elem, onectype):
        """
        раздает имена по настройке
        пока что только для главного окна
        потом стоит продумать вложенность
        """
        try:
            for key, value in onectypes[onectype].items():
                if value.startswith('#'):  # это вложенный элемент, внутри него тоже можно раздавать имена
                    current = getxmlbyindexes(elem, [key, ])
                    current.set('name',
                                value[1:])  # не совсем правильно. Имя и тип - разные вещи. Здесь может не хватить словаря.
                    self.givenames(current, value[1:])
                else:
                    getxmlbyindexes(elem, [key, ]).set('name', value)
        except:
            pass
            # print('Не удалось дать имя: ',value)

from enum import Enum
class subFolders(Enum):
    # "перечисление" с именами вложенных каталогов для src
    binary = 'Макеты'
    forms = 'Формы'
    other = 'Прочее'

class ReplacesForForms:
    """перечень замен, произведенных над исходным текстом формы
    Пожалуй, стоит перевести на структуру, или вообще встроить в родительский класс"""
    replacenumber = 0
    textreplaces = {}
    base64replaces = {}

def preparetypes():
    """
    Готовит расшифровку внутренней структуры форм 1С
    """

    global onectypes

    onectypes['Форма'] = \
        {1: '#ГлавноеОкно',
        2: '#РеквизитыФормы',
        3: '#_СохранениеЗначений',
        6: 'СостояниеОкна',
        7: 'ПоложениеПрикрепленногоОкна',
        8: 'СоединяемоеОкно',
        9: 'ПоложениеОкна',
        10: 'ИзменениеРазмера',
        15: 'ИзменятьСпособОтображенияОкна',
        14: 'СпособОтображенияОкна',
        17: 'РежимРабочегоСтола',
        18: 'РазрешитьЗакрытие',
        19: 'ПроверятьЗаполнениеАвтоматически'} # кажется, это как раз из новых платформ

    onectypes['ГлавноеОкно'] = \
        {2: '#_ЭлементыФормы',
        3: 'Ширина',
        4: 'Высота',
        6: 'Использовать сетку',
        7: 'Использовать выравнивающие линии',
        8: 'Горизонтальный шак сетки',
        9: 'Вертикальный шаг сетки',
        10: 'Счетчик сохранений'}

    onectypes['РеквизитыФормы'] = \
        {}

    onectypes['_ЭлементыФормы'] = \
        {1: '#_ЭлементыФормы2'}

    onectypes['_ЭлементыФормы2'] = \
        {1: '#_ЭлементыФормы3'}

    onectypes['_ЭлементыФормы3'] = \
        {0: '#_Цвета', # здесь как-то потерялись еще 5 элементов. Как это случилось? Кажется, все-таки надо учитывать количество значений впереди.
        8: '#_Картинки',
        9: 'ОтображениеЗакладок',
        10: 'РаспределятьПоСтраницам',
        11: '#_СтраницыФормы',
        12: 'АвтоПравила',
        13: 'АвтоПорядокОбхода',
        15: '_КоличествоОписанийПривязокВпереди'} # дальше будет идти именно столько групп с привязками. Можно попробовать задавать их номерами, типа "17..17КоличествоОписанийПривязок"
        # Можно ввести виртуальный элемент - "количество элементов типа <> впереди". А сами элементы делать вложенным тегом. Ядреный алгоритм.

    onectypes['_СтраницыФормы'] = \
        {1: '_КоличествоСтраниц'} # Дальше интеренсно. 3 и 4 будут краткими описаниями страниц, а 5 и 6 - полными

    onectypes['_СохранениеЗначений'] = \
        {2: '#_СохранениеЗначений1'}

    onectypes['_Цвета'] = \
        {2: '#_Цвет'} # уткнулся в то, что имя - отдельно, тип - отдельно. Это поле - цвет фона, например

    onectypes['_Цвет'] = \
        {}

    onectypes['_Картинки'] = \
        {1: 'РазмерКартинки'}

    onectypes['_СохранениеЗначений1'] = \
        {1: 'СохранятьЗначения',
        4: 'ВосстанавливатьЗначенияПриОткрытии'}

    onectypes['_root_second'] = \
        {3: '#_root_second_2'} # описание файла, на который ссылается root

    onectypes['_root_second_2'] = \
        {1: '#_root_second_3'}

    onectypes['_root_second_3'] = \
        {5: '#_root_second_ФормыОбработки',
         6: '#_root_second_РеквизитыОбработки'}

    onectypes['_root_second_ФормыОбработки'] = \
        {0: '_UUID_метка_что_это_формы',
         1: 'КоличествоФорм'} # А дальше идут гуиды самих форм. Вот отсюда мы можем уже обращаться к именам файлов.
    # И в этом файле не понять, какие из них обычные, а какие управляемые

    onectypes['_root_second_РеквизитыОбработки'] = \
        {}

class epfParser(object):

    filename = None
    filename_short = None
    unpacked_dir = None
    source_dir = None
    curdir = None

    def __enter__(self):
        return self
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __init__(self, filename=''):
        self.filename = filename
        self.filename_short = os.path.basename(self.filename)[:-4]
        self.curdir = os.path.dirname(self.filename)
        self.unpacked_dir = os.path.join(self.curdir, self.filename_short + '.und')  # сюда распакуем v8reader'ом
        self.source_dir = os.path.join(self.curdir, 'src', self.filename_short)  # а сюда переложим красивые файлы

        # и сразу прочитаем

    def prepareDirsForUnpack(self):
        """Чистит предыдущую раскладку и заново создает каталоги для исходников, если надо"""
        if not os.path.exists(os.path.join(self.curdir, 'src')):
            os.mkdir(os.path.join(self.curdir, 'src')) # создать общую папку src, если ее нет

        if os.path.exists(self.unpacked_dir):
            shutil.rmtree(self.unpacked_dir)  # почистим предыдущую раскладку

        if os.path.exists(self.source_dir):
            shutil.rmtree(self.source_dir)  # почистим предыдущую раскладку

        os.mkdir(self.source_dir)  # и создадим структуру каталогов
        for i in subFolders:
            os.mkdir(os.path.join(self.source_dir, i.value))

    def process_epf(self):
        """
        раскладывает отдельно взятый файл epf на исходники
        """
        preparetypes()  # подготовим описание форм в 1С
        self.prepareDirsForUnpack()  # подготовим каталоги

        commandText = '"UnpackV8.exe" -parse "%s" "%s"' % (self.filename, self.unpacked_dir)
        _localExec(commandText)  # распакуем файл

        # модуль объекта
        for fn in glob.glob(self.unpacked_dir + '/*/text'):
            # предполагаем, что для  epf он только один
            newfilename = os.path.join(self.source_dir, 'МодульОбъекта.1s')
            os.rename(fn, newfilename)
            if settings['split_module_text']: # раскидаем текст модуля на отдельные файлы
                with Profiler('split', profiler_results) as p:
                    extract_one(newfilename)

        # этот файл всегда есть в epf, он короткий, задает только идентификатор текущего объекта. Все кишки лежат в файле с именем, равным этому идентификатору
        with ones_object(os.path.join(self.unpacked_dir, 'root')) as rootfile_array:
            descriptionsfilename = rootfile_array.object_as_list[1].replace('_', '-')
            # print('descriptions are here: ' + descriptionsfilename)

        # Этот файл содержит список форм, реквизитов, табличных частей и т.д.
        # Найдем описания форм и переберем их циклом
        with ones_object(os.path.join(self.unpacked_dir, descriptionsfilename), '_root_second') as second_root_file:
            second_root_file.serialize()  # распарсим данный файл по описанию _root_second
            # формы лежат в 3-1-5, в количестве 3-1-5-1, начиная с 3-1-5-2

            forms = []  # подготовим массивы под обычные и управляемые формы
            for form_id in second_root_file.value_by_address('3-1-5')[2:]:

                formfilename = os.path.join(self.unpacked_dir, form_id.replace('_', '-'))
                with ones_object(formfilename) as short_form_desc:
                    # short_form_desc.serialize(formfilename+'.xml')
                    # Признак "управляемая - обычная" находится по адресу 1-1-1-3
                    # pass
                    form_name = short_form_desc.value_by_address(
                        '1-1-1-1-2')  # восстанавливать замены непосредственно внутри объекта, после получения массива

                # а дальше будет formfilename+'.0' - папка, если обычная форма, и файл, если управляемая.
                if os.path.isdir(formfilename + '.0'):
                    _fn, _type = os.path.join(formfilename + '.0', 'form'), 'Форма'
                else:
                    _fn, _type = formfilename + '.0', 'УправляемаяФорма'

                forms.append([_fn, _type, self.source_dir, form_name.replace('"', '')])

            # теперь каждую форму из полученного списка преобразуем в xml
            # """Многопоточный вариант"""
            from multiprocessing import cpu_count
            # создадим столько воркеров, сколько ядер у нашего процессора
            with Pool(cpu_count()) as p:
                p.starmap(parse_and_move_single_file, forms)

            # """Вариант в один поток, для отладки, 10 форм"""
            # for form in forms:
            #     # parse_and_move_single_file(form)
            #     if form[1] == 'УправляемаяФорма':
            #         parse_and_move_single_file(form[0],form[1],form[2],form[3])

            # обработка макетов
            for maket_id in second_root_file.value_by_address('3-1-4')[2:]:
                # макеты лежат по адресу 3-1-4, начиная с элемента 2
                fn = os.path.join(self.unpacked_dir, maket_id.replace('_', '-'))
                with ones_object(fn) as maket_id_obj:
                    # print('maket ' + maket_id)
                    sinonym = maket_id_obj.value_by_address('1-2-2')
                    newfilename = os.path.join(self.source_dir, subFolders.binary.value, sinonym.replace('"', ''))
                    os.rename(fn + '.0', newfilename)

                    # TODO сделать одну общую процедуру по обработке элемента: неважно, макет, обычная/управляемая форма и т.д.

        # Теперь переберем остальные файлы здесь же
        parse_and_move_single_file(os.path.join(self.unpacked_dir, 'root'), '')
        parse_and_move_single_file(os.path.join(self.unpacked_dir, 'version'), '')  # здесь лежит описание - 8.1 или 8.2
        parse_and_move_single_file(os.path.join(self.unpacked_dir, 'versions'),'')  # здесь еще надо будет строки отсортировать

        # все, что осталось внутри, просто переместим в папку "Прочее"
        for fn in glob.glob(os.path.join(self.unpacked_dir,'*')):
            newfilename = os.path.join(self.source_dir, 'Прочее', os.path.basename(fn))
            os.rename(fn, newfilename)

        os.rmdir(self.unpacked_dir)  # и удалим папку с распаковкой, т.к. она в этот момент должна быть полностью разобрана

        after_parse_custom_actions_UF(self.source_dir)  # какие-то еще действия, не относящиеся напрямую к логике разбора


def parse_and_move_single_file(filename='', type='', dest='', object_name=''):
    """преобразует файл в читаемый формат и выносит его в нужную папку в dest"""
    with ones_object(filename, type) as obj:

        if type in ['Форма', 'УправляемаяФорма']:
            dest = os.path.join(dest, subFolders.forms.value)
            dest_filename = os.path.join(dest, object_name, 'form')
            # Создадим каталог под них
            if not os.path.exists(os.path.join(dest, object_name)):
                os.mkdir(
                    os.path.join(dest, object_name))  # создадим каталог под данную форму, если его вдруг нет
        else:
            dest = os.path.join(dest, subFolders.other.value)  # по умолчанию

        if obj.ones_type == 'Форма':

            obj.object_as_list[1][10] = 123  # скинем счетчик сохранений
            name_of_module_file = os.path.join(os.path.dirname(filename), 'module')
            new_module_file = os.path.join(dest, object_name, 'module.1s')

            os.rename(name_of_module_file, new_module_file)
            if settings['split_module_text']:
                with Profiler('split', profiler_results) as p:
                    extract_one(new_module_file)

            os.remove(filename)  # удаляем старый файл формы
            obj.serialize(os.path.join(dest, object_name, 'form'))  # вместо него пишем новый, распарсенный

        elif obj.ones_type == 'УправляемаяФорма':

            text_of_module = obj.value_by_address('2')
            obj.object_as_list[2] = '#extracted#'
            text_of_module = text_of_module[1:-1].replace('""',
                                                          '"')  # двойные кавычки заменим на одинарные. Открывающую и закрывающую кавычку выкинем.
            name_of_module_file = os.path.join(dest, object_name, 'module.1s')
            open(name_of_module_file, 'w', encoding='utf-8').write(text_of_module)
            if settings['split_module_text']:
                with Profiler('split', profiler_results) as p:
                    extract_one(name_of_module_file)

            os.remove(filename)  # удаляем старый файл формы
            obj.serialize(os.path.join(dest, object_name, 'form'))  # вместо него пишем новый, распарсенный


def after_parse_custom_actions(source_dir):
    """после полной раскладки epf на исходники сделаем что-нибудь еще"""
    suffixes = ['_epf', '_xsd', '_cf']
    for fileName in glob.glob(os.path.join(source_dir, subFolders.binary.value, '*')):
        for suffix in suffixes:
            if fileName.endswith(suffix):
                end = suffix[1:]
                name = fileName[:-(len(suffix))] # отрежем окончание
                newFileName = name + '.' + end

                try:
                    extract_base64(fileName,newFileName)
                    os.remove(fileName)  # а исходный удалить
                    if (end == 'epf') and settings['extract_included_epf']:
                        epfParser(newFileName).process_epf()
                except:
                    print('не разобрались с '+fileName)

def after_parse_custom_actions_UF(source_dir):
    """после полной раскладки epf на исходники сделаем что-нибудь еще"""
    for fileName in glob.glob(os.path.join(source_dir, subFolders.binary.value, '*')):

        if os.path.basename(fileName).startswith('Модуль_'):
            newFileName = fileName + '.epf'

            try:
                extract_base64(fileName,newFileName)
                os.remove(fileName)  # а исходный удалить
                if settings['extract_included_epf']:
                    epfParser(newFileName).process_epf()
            except:
                print('не разобрались с '+fileName)




def main():

    #parser = argparse.ArgumentParser(description='Распаковка внешних обработок 1C8')
    # parser.add_argument('--version', action='version', version='%(prog)s {}'.format(__version__))
    # parser.add_argument('-v', '--verbose', dest='verbose_count', action='count', default=0,
    #                     help='Increases log verbosity for each occurence.')
    # parser.add_argument('--index', action='store_true', default=False, help='Добавляем в индекс исходники')
    # parser.add_argument('--g', action='store_true', default=False,
    #                     help='Запустить чтение индекса из git и определить список файлов для разбора')
    # parser.add_argument('--compile', action='store_true', default=False, help='Собрать внешний файл/обработку')
    # parser.add_argument('--type', action='store', default='auto',
    #                     help='Тип файла для сборки epf, erf. По умолчанию авто epf')
    # parser.add_argument('--platform', action='store', help='Путь к платформе 1С')
    # parser.add_argument('inputPath', nargs='?', help='Путь к файлам необходимым для распаковки')
    # parser.add_argument('output', nargs='?', help='Путь к каталогу, куда распаковывать')
    #
    #
    #
    # args = parser.parse_args()
    #
    # if args.g is True:

    """Что хочется:
    1) При запуске без параметров - рекурсивно декомпилируется все вокруг (epf)
    2) Есть возможность декомпилировать отдельно взятый файл epf
    3) Есть возможность указать, надо ли декомпилировать вложенные макеты (и как их определять? в другом скрипте? в файле настроек?
    4) Возможность декомпилировать отдельный файл из уже разобранного EPF - файл формы, файл root и т.д."""


    parser = argparse.ArgumentParser(description='Распаковка внешних обработок 1C8')
    parser.add_argument('--action', action='store', default='decompile',
                        help='Действие.'
                             '  "decompile" разберет все файлы epf, находящиеся в текущей папке, на исходники'
                             '  По умолчанию - "decompile"')
    args = parser.parse_args()
    if args.action == 'decompile':
        with Profiler('process_epf', profiler_results) as p:
            for fn in glob.glob(os.path.join(os.getcwd(), '*.epf')):
                # предполагаем, что для  epf он только один
                print('parse: ' + os.path.basename(fn)[:-4])
                epfParser(fn).process_epf()
    else:
        print('что делать?')




def extract_base64(fileName,newFileName):
    """Извлекает base64 значение из распакованного файла с макетом fileName
    и записывает извлеченный бинарник в newFileName"""
    import base64
    with ones_object(fileName) as decompiled:
        _str = decompiled.value_by_address('1').replace('\n','').replace('\r','')
        len_of_beginning = len(r'{#base64:')
        len_of_end = len(r'}')
        bin = base64.b64decode(_str[len_of_beginning:-len_of_end], validate=True)
        open(newFileName, 'wb').write(bin)

def debug():
    # filename = 'C:\\Users\\volodkindv\\Documents\\SVN 1C\\onecDecoder\\test.und\\da7349ba-2d62-46a6-b53f-f0eca2fc8427.0'

    # epfParser(fn).process_epf()
    # parse_and_move_single_file(filename, 'Форма')
    pass


if __name__ == "__main__":

    main()
    # debug()

    Profiler.print_results(profiler_results)
