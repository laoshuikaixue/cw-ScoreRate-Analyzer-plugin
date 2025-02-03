import json
import time
from datetime import datetime, timedelta

import requests
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QWidget, QVBoxLayout, QScrollBar
from loguru import logger
from qfluentwidgets import isDarkTheme

from .ClassWidgets.base import PluginConfig, PluginBase

WIDGET_CODE = 'ScoreRate_Analyzer_widget.ui'
WIDGET_NAME = '作业得分率分析 | LaoShui'
WIDGET_WIDTH = 245

CACHE_DURATION = 1800  # 缓存更新周期：30分钟


class SmoothScrollBar(QScrollBar):
    """平滑滚动条"""
    scrollFinished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ani = QPropertyAnimation()
        self.ani.setTargetObject(self)
        self.ani.setPropertyName(b"value")
        self.ani.setEasingCurve(QEasingCurve.OutCubic)
        self.ani.setDuration(400)  # 设置动画持续时间
        self.__value = self.value()
        self.ani.finished.connect(self.scrollFinished)

    def setValue(self, value: int):
        if value == self.value():
            return

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
        if hasattr(self.vScrollBar, 'scrollValue'):
            self.vScrollBar.scrollValue(-e.angleDelta().y())


class Plugin(PluginBase):
    def __init__(self, cw_contexts, method):
        super().__init__(cw_contexts, method)
        self.cw_contexts = cw_contexts
        self.method = method

        self.CONFIG_PATH = f'{cw_contexts["PLUGIN_PATH"]}/config.json'
        self.PATH = cw_contexts['PLUGIN_PATH']
        self.cfg = PluginConfig(self.PATH, 'config.json')
        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)

        self.headers = self.load_headers()
        self.previous_data = None  # 用于缓存历史数据

        # 定时器配置
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_score_data)
        self.timer.start(CACHE_DURATION * 1000)

        # 滚动相关配置
        self.scroll_position = 0
        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.auto_scroll)
        self.scroll_timer.start(30)

    def load_headers(self):
        """加载认证头信息"""
        try:
            with open(self.CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                config['headers']['t'] = str(int(time.time()))  # 更新请求头中的t字段为当前时间戳
                return config['headers']
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}

    def load_params(self, start_date, end_date):
        """加载请求参数"""
        try:
            with open(self.CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                params = config.get('params', {})
                params['startDate'] = start_date
                params['endDate'] = end_date
                return params
        except Exception as e:
            logger.error(f"加载请求参数失败: {e}")
            return {}

    def fetch_score_data(self):
        """获取得分率数据"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        params = self.load_params(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        )
        # params = self.load_params("2024-12-29", "2025-01-01")  # 调试用

        try:
            # 第一阶段：GET获取作业ID数据
            response_get = requests.get(
                'https://www.xinjiaoyu.com/api/v3/server_homework/homework/class/score/rate',
                headers=self.headers,
                params=params
            )
            if response_get.status_code != 200:
                raise Exception(f'GET请求失败，状态码：{response_get.status_code}')

            data = response_get.json()
            # print(data)

            if not data.get('data') or not data['data'].get('homeworkCourseVOList'):
                raise Exception('数据为空或格式不正确')

            # 第二阶段：POST获取作业详细得分率数据
            template_ids = [str(homework['templateId']) for course in data['data']['homeworkCourseVOList'] for homework
                            in course['homeworkVOList'] if homework['templateId'] is not None]
            homework_ids = [homework_id for course in data['data']['homeworkCourseVOList'] for homework in
                            course['homeworkVOList'] for homework_id in homework['homeworkIds']]
            class_ids = [cls['classId'] for cls in data['data']['classVOList']]

            payload = {
                'schoolId': params['schoolId'],
                'gradeId': params['gradeId'],
                'templateIds': template_ids,
                'homeworkIds': homework_ids,
                'schoolYearName': params['schoolYearName'],
                'isHistory': params['isHistory'].lower() == 'true',
                'classIds': class_ids
            }

            response_post = requests.post(
                'https://www.xinjiaoyu.com/api/v3/server_homework/homework/answer/sheet/class/score/rate',
                headers=self.headers,
                json=payload
            )
            if response_post.status_code != 200:
                raise Exception(f'POST请求失败，状态码：{response_post.status_code}')
            # print(response_post.json())

            return self.process_data(response_post.json())

        except Exception as e:
            logger.error(f"数据获取失败: {e}")
            return self.previous_data if self.previous_data else f"更新时间: {datetime.now().strftime('%H:%M:%S')}\n暂无可用数据"

    def process_data(self, post_data):
        """处理并格式化数据"""
        # 构建科目映射关系：courseId -> courseName
        course_list = post_data["data"]["courseVOList"]
        course_mapping = {course["courseId"]: course["courseName"] for course in course_list}

        content = []

        def get_rate(course_score_rate_list, target_course_id):
            for item in course_score_rate_list:
                if item.get("courseId") == target_course_id:
                    return item.get("scoreRate")
            return None

        # 处理全年级数据（classId 为 "-1"）- 全科和单科
        grade_data = next((item for item in post_data['data']['scoreRateVOList'] if item['classId'] == "-1"), None)
        if grade_data:
            content.append(f"数据更新时间: {datetime.now().strftime('%H:%M:%S')}")
            content.append("近七天年级段作业得分率数据\n【全年级】")
            full_rate = get_rate(grade_data['courseScoreRate'], "-1")
            content.append(f"全科得分率：{self.format_rate(full_rate)}")
            content.append("单科得分率：")
            for course in grade_data['courseScoreRate']:
                if course['courseId'] == "-1":
                    continue
                name = course_mapping.get(course['courseId'], "未知科目")
                content.append(f"{name}：{self.format_rate(course['scoreRate'])}")

        # 处理班级数据
        content.append("\n【各班级】")
        for class_data in post_data['data']['scoreRateVOList']:
            if class_data['classId'] == "-1":
                continue
            content.append(f"\n班级：{class_data['className']}")
            full_rate = get_rate(class_data['courseScoreRate'], "-1")
            content.append(f"全科得分率：{self.format_rate(full_rate)}")
            content.append("单科得分率：")
            for course in class_data['courseScoreRate']:
                if course['courseId'] == "-1":
                    continue
                name = course_mapping.get(course['courseId'], "未知科目")
                content.append(f"  {name}：{self.format_rate(course['scoreRate'])}")

        self.previous_data = '\n'.join(content)
        return self.previous_data

    @staticmethod
    def format_rate(rate):
        """格式化得分率显示"""
        return f"{rate}%" if rate != "-" else rate

    def update_score_data(self):
        """更新数据并刷新界面"""
        data = self.fetch_score_data()
        self.update_widget_content(data)
        self.method.change_widget_content(WIDGET_CODE, WIDGET_NAME, WIDGET_NAME)
        logger.info("得分率数据已更新")

    def update_widget_content(self, content):
        """更新小组件显示内容"""
        widget = self.method.get_widget(WIDGET_CODE)
        if not widget:
            logger.error("小组件未找到")
            return

        layout = self.find_child_layout(widget, 'contentLayout')
        if not layout:
            logger.error("布局未找到")
            return

        self.clear_existing_content(layout)
        scroll_area = self.create_scroll_area(content)
        layout.addWidget(scroll_area)

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
            font-size: 20px;
            color: {font_color};
            padding: 10px;
            font-weight: bold;
            background: none;
        """)
        scroll_content_layout.addWidget(content_label)

        scroll_area.setWidget(scroll_content)
        return scroll_area

    @staticmethod
    def find_child_layout(widget, name):
        """查找子布局"""
        return widget.findChild(QHBoxLayout, name)

    @staticmethod
    def clear_existing_content(layout):
        """清空布局内容"""
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    def auto_scroll(self):
        """自动滚动功能"""
        widget = self.method.get_widget(WIDGET_CODE)
        if not widget:
            return

        scroll_area = widget.findChild(SmoothScrollArea)
        if not scroll_area:
            return

        scrollbar = scroll_area.verticalScrollBar()
        max_pos = scrollbar.maximum()

        if self.scroll_position >= max_pos:
            self.scroll_position = 0
        else:
            self.scroll_position += 1

        scrollbar.setValue(self.scroll_position)

    def execute(self):
        """初始执行"""
        self.update_score_data()
