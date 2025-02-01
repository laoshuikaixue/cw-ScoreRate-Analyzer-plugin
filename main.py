import json
import time
import threading

import requests
from PyQt5.QtCore import QPropertyAnimation, QEasingCurve, QTimer
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QScrollBar, QScrollArea, QWidget, QVBoxLayout
from loguru import logger
from qfluentwidgets import isDarkTheme

from .ClassWidgets.base import PluginConfig, PluginBase

# 自定义小组件
WIDGET_CODE = 'ScoreRate_Analyzer_widget.ui'
WIDGET_NAME = '班级得分率分析 | LaoShui'
WIDGET_WIDTH = 245


class SmoothScrollBar(QScrollBar):
    """平滑滚动条"""
    scrollFinished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ani = QPropertyAnimation(self, b"value")
        self.ani.setEasingCurve(QEasingCurve.OutCubic)
        self.ani.setDuration(400)  # 设置动画持续时间
        self.ani.finished.connect(self.scrollFinished)

    def setValue(self, value: int):
        if value != self.value():
            self.ani.stop()
            self.scrollFinished.emit()
            self.ani.setStartValue(self.value())
            self.ani.setEndValue(value)
            self.ani.start()

    def wheelEvent(self, e):
        e.ignore()  # 阻止默认滚轮事件


class SmoothScrollArea(QScrollArea):
    """平滑滚动区域"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vScrollBar = SmoothScrollBar()
        self.setVerticalScrollBar(self.vScrollBar)
        self.setStyleSheet("QScrollBar:vertical { width: 0px; }")  # 隐藏滚动条

    def wheelEvent(self, e):
        if hasattr(self.vScrollBar, 'setValue'):
            self.vScrollBar.setValue(self.vScrollBar.value() - e.angleDelta().y())


class Plugin(PluginBase):
    def __init__(self, cw_contexts, method):
        super().__init__(cw_contexts, method)
        self.cw_contexts = cw_contexts
        self.method = method
        self.CONFIG_PATH = f'{cw_contexts["PLUGIN_PATH"]}/config.json'
        self.PATH = cw_contexts['PLUGIN_PATH']
        self.cfg = PluginConfig(self.PATH, 'config.json')

        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)

        self.scroll_position = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.auto_scroll)
        self.timer.start(30)  # 设置滚动速度

        self.headers = self.load_headers()  # 加载请求头数据
        self.previous_data = None  # 保存上一次成功获取的数据

        self.start_auto_update_thread()  # 启动自动更新线程

    def load_headers(self):
        """加载请求头数据"""
        try:
            with open(self.CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                config['headers']['t'] = str(int(time.time()))  # 更新请求头中的t字段为当前时间戳
                return config['headers']
        except FileNotFoundError:
            logger.error("未找到 config.json 文件，请先设置请求头数据!")
            return {}
        except KeyError:
            logger.error("config.json 中缺少 headers 键!")
            return {}

    def fetch_and_update_data(self):
        """获取并更新数据"""
        # GET请求参数
        params_get = {
            'schoolId': '123204',
            'gradeId': '10024',
            'schoolYearName': '2024-2025',
            'isHistory': 'false',
            'startDate': '2024-12-29',
            'endDate': '2025-01-01'
        }

        try:
            # 发送GET请求
            response_get = requests.get(
                'https://www.xinjiaoyu.com/api/v3/server_homework/homework/class/score/rate',
                headers=self.headers,
                params=params_get
            )

            # 检查GET请求是否成功
            if response_get.status_code != 200:
                raise Exception(f'GET请求失败，状态码：{response_get.status_code}')

            data = response_get.json()

            # 提取templateIds和homeworkIds
            template_ids = [str(homework['templateId']) for course in data['data']['homeworkCourseVOList'] for homework
                            in
                            course['homeworkVOList'] if homework['templateId'] is not None]
            homework_ids = [homework_id for course in data['data']['homeworkCourseVOList'] for homework in
                            course['homeworkVOList'] for homework_id in homework['homeworkIds']]
            class_ids = [cls['classId'] for cls in data['data']['classVOList']]

            # 构造POST请求负载
            payload = {
                'schoolId': params_get['schoolId'],
                'gradeId': params_get['gradeId'],
                'templateIds': template_ids,
                'homeworkIds': homework_ids,
                'schoolYearName': params_get['schoolYearName'],
                'isHistory': params_get['isHistory'].lower() == 'true',
                'classIds': class_ids
            }

            # 发送POST请求
            response_post = requests.post(
                'https://www.xinjiaoyu.com/api/v3/server_homework/homework/answer/sheet/class/score/rate',
                headers=self.headers,
                json=payload
            )

            if response_post.status_code != 200:
                raise Exception(f'POST请求失败，状态码：{response_post.status_code}')

            return response_post.json()

        except Exception as ex:
            logger.error(f"获取数据失败: {ex}")
            if self.previous_data:
                logger.info("使用之前成功获取的数据")
                return self.previous_data
            else:
                raise Exception("无法获取数据且没有之前的数据可用")

    def update_widget_content(self):
        """更新小组件内容"""
        data = self.fetch_and_update_data()
        self.previous_data = data  # 保存成功获取的数据

        # 构建科目映射关系：courseId -> courseName
        course_list = data["data"]["courseVOList"]
        course_mapping = {course["courseId"]: course["courseName"] for course in course_list}

        def get_score_rate(course_score_rate_list, target_course_id):
            for item in course_score_rate_list:
                if item.get("courseId") == target_course_id:
                    return item.get("scoreRate")
            return None

        # 构建显示内容
        content = ""

        # 处理全年级数据（classId 为 "-1"）- 全科和单科
        overall_data = next((item for item in data["data"]["scoreRateVOList"] if item.get("classId") == "-1"), None)
        if overall_data:
            content += "【全年级】\n"
            overall_full_subject_rate = get_score_rate(overall_data.get("courseScoreRate", []), "-1")
            overall_full_subject_rate_str = f"{overall_full_subject_rate}%" if overall_full_subject_rate != "-" else\
                overall_full_subject_rate
            content += f"全科得分率：{overall_full_subject_rate_str}\n单科得分率：\n"
            for record in overall_data.get("courseScoreRate", []):
                course_id = record.get("courseId")
                if course_id == "-1":
                    continue
                course_name = course_mapping.get(course_id, f"未知科目({course_id})")
                score_rate = record.get("scoreRate")
                score_rate_str = f"{score_rate}%" if score_rate != "-" else score_rate
                content += f"{course_name}：{score_rate_str}\n"
            content += "\n"
        else:
            content += "未找到全年级的数据！\n"

        # 输出全科和单科得分率
        content += "【各班级】\n"
        for class_data in data["data"]["scoreRateVOList"]:
            if class_data.get("classId") == "-1":
                continue
            class_name = class_data.get("className")
            content += f"班级：{class_name}\n"
            full_subject_rate = get_score_rate(class_data.get("courseScoreRate", []), "-1")
            full_subject_rate_str = f"{full_subject_rate}%" if full_subject_rate != "-" else full_subject_rate
            content += f"全科得分率：{full_subject_rate_str}\n单科得分率：\n"
            for record in class_data.get("courseScoreRate", []):
                course_id = record.get("courseId")
                if course_id == "-1":
                    continue
                course_name = course_mapping.get(course_id, f"未知科目({course_id})")
                score_rate = record.get("scoreRate")
                score_rate_str = f"{score_rate}%" if score_rate != "-" else score_rate
                content += f"   {course_name}：{score_rate_str}\n"
            content += "\n"

        print(content)

        self.test_widget = self.method.get_widget(WIDGET_CODE)
        if not self.test_widget:
            logger.error(f"未找到小组件，WIDGET_CODE: {WIDGET_CODE}")
            return

        content_layout = self.find_child_layout(self.test_widget, 'contentLayout')
        if not content_layout:
            logger.error("未能找到小组件的'contentLayout'布局")
            return

        content_layout.setSpacing(5)
        self.method.change_widget_content(WIDGET_CODE, WIDGET_NAME, WIDGET_NAME)
        self.clear_existing_content(content_layout)

        scroll_area = self.create_scroll_area(content)
        if scroll_area:
            content_layout.addWidget(scroll_area)
            logger.success('得分率信息更新成功！')
        else:
            logger.error("滚动区域创建失败")

    @staticmethod
    def find_child_layout(widget, layout_name):
        """根据名称查找并返回布局"""
        return widget.findChild(QHBoxLayout, layout_name)

    def create_scroll_area(self, content):
        scroll_area = SmoothScrollArea()
        scroll_area.setWidgetResizable(True)

        scroll_content = QWidget()
        scroll_content_layout = QVBoxLayout()
        scroll_content.setLayout(scroll_content_layout)
        self.clear_existing_content(scroll_content_layout)

        font_color = "#FFFFFF" if isDarkTheme() else "#000000"
        content_label = QLabel(content)
        content_label.setAlignment(Qt.AlignLeft)  # 设置文字为左对齐
        content_label.setWordWrap(True)
        content_label.setStyleSheet(f"""
            font-size: 18px;
            color: {font_color};
            padding: 10px;
            font-weight: bold;
            background: none;
        """)
        scroll_content_layout.addWidget(content_label)

        scroll_area.setWidget(scroll_content)
        return scroll_area

    @staticmethod
    def clear_existing_content(content_layout):
        """清除布局中的旧内容"""
        while content_layout.count() > 0:
            item = content_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()  # 确保子组件被销毁

    def auto_scroll(self):
        """自动滚动功能"""
        if not self.test_widget:
            return

        scroll_area = self.test_widget.findChild(SmoothScrollArea)
        if not scroll_area:
            # logger.warning("无法找到 SmoothScrollArea，停止自动滚动") # 不log了 不然要被刷屏了
            return

        vertical_scrollbar = scroll_area.verticalScrollBar()
        if not vertical_scrollbar:
            # logger.warning("无法找到垂直滚动条，停止自动滚动") # 不log了 不然要被刷屏了
            return

        max_value = vertical_scrollbar.maximum()
        self.scroll_position = 0 if self.scroll_position >= max_value else self.scroll_position + 1
        vertical_scrollbar.setValue(self.scroll_position)

    def start_auto_update_thread(self):
        """启动自动更新线程"""

        def auto_update():
            while True:
                try:
                    self.update_widget_content()
                except Exception as ex:
                    logger.error(f"自动更新失败: {ex}")
                time.sleep(1800)  # 每30分钟更新一次

        thread = threading.Thread(target=auto_update)
        thread.daemon = True  # 设置为守护线程，在主线程退出时自动结束
        thread.start()

    def update(self, cw_contexts):  # 每秒更新一次
        super().update(cw_contexts)
        self.cfg.update_config()

    def execute(self):  # 自启动执行
        self.update_widget_content()
