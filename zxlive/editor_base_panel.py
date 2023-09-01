from __future__ import annotations

import copy
from enum import Enum
from fractions import Fraction
from typing import Callable, Iterator, TypedDict

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QInputDialog, QListView, QListWidget,
                               QListWidgetItem, QSplitter, QToolButton)
from pyzx import EdgeType, VertexType
from pyzx.utils import get_w_partner, vertex_is_w
from sympy import sympify

from .base_panel import BasePanel, ToolbarSection
from .commands import (AddEdge, AddNode, AddWNode, ChangeEdgeColor,
                       ChangeNodeType, ChangePhase, MoveNode, SetGraph,
                       UpdateGraph)
from .common import VT, GraphT, ToolType, get_data
from .dialogs import show_error_msg
from .eitem import HAD_EDGE_BLUE
from .graphscene import EditGraphScene
from .vitem import BLACK, H_YELLOW, ZX_GREEN, ZX_RED


class ShapeType(Enum):
    CIRCLE = 1
    SQUARE = 2
    TRIANGLE = 3
    LINE = 4
    DASHED_LINE = 5

class DrawPanelNodeType(TypedDict):
    text: str
    icon: tuple[ShapeType, str]


VERTICES: dict[VertexType.Type, DrawPanelNodeType] = {
    VertexType.Z: {"text": "Z spider", "icon": (ShapeType.CIRCLE, ZX_GREEN)},
    VertexType.X: {"text": "X spider", "icon": (ShapeType.CIRCLE, ZX_RED)},
    VertexType.H_BOX: {"text": "H box", "icon": (ShapeType.SQUARE, H_YELLOW)},
    VertexType.BOUNDARY: {"text": "boundary", "icon": (ShapeType.CIRCLE, BLACK)},
    VertexType.W_OUTPUT: {"text": "W node", "icon": (ShapeType.TRIANGLE, BLACK)},
}

EDGES: dict[EdgeType.Type, DrawPanelNodeType] = {
    EdgeType.SIMPLE: {"text": "Simple", "icon": (ShapeType.LINE, BLACK)},
    EdgeType.HADAMARD: {"text": "Hadamard", "icon": (ShapeType.DASHED_LINE, HAD_EDGE_BLUE)},
}


class EditorBasePanel(BasePanel):
    """Base class implementing the shared functionality of graph edit
    and rule edit panels of ZX live."""

    graph_scene: EditGraphScene
    start_derivation_signal = Signal(object)

    _curr_ety: EdgeType.Type
    _curr_vty: VertexType.Type

    def __init__(self, *actions: QAction) -> None:
        super().__init__(*actions)
        self._curr_vty = VertexType.Z
        self._curr_ety = EdgeType.SIMPLE

    def _toolbar_sections(self) -> Iterator[ToolbarSection]:
        yield toolbar_select_node_edge(self)
        yield ToolbarSection(*self.actions)

    def create_side_bar(self) -> QSplitter:
        sidebar = QSplitter(self)
        sidebar.setOrientation(Qt.Orientation.Vertical)
        vertex_list = create_list_widget(self, VERTICES, self._vty_clicked)
        edge_list = create_list_widget(self, EDGES, self._ety_clicked)
        sidebar.addWidget(vertex_list)
        sidebar.addWidget(edge_list)
        return sidebar

    def _tool_clicked(self, tool: ToolType) -> None:
        self.graph_scene.curr_tool = tool

    def _vty_clicked(self, vty: VertexType.Type) -> None:
        self._curr_vty = vty
        selected = list(self.graph_scene.selected_vertices)
        if len(selected) > 0:
            cmd = ChangeNodeType(self.graph_view, selected, vty)
            self.undo_stack.push(cmd)

    def _ety_clicked(self, ety: EdgeType.Type) -> None:
        self._curr_ety = ety
        self.graph_scene.curr_ety = ety
        selected = list(self.graph_scene.selected_edges)
        if len(selected) > 0:
            cmd = ChangeEdgeColor(self.graph_view, selected, ety)
            self.undo_stack.push(cmd)

    def paste_graph(self, graph: GraphT) -> None:
        if graph is None: return
        new_g = copy.deepcopy(self.graph_scene.g)
        new_verts, new_edges = new_g.merge(graph.translate(0.5, 0.5))
        cmd = UpdateGraph(self.graph_view,new_g)
        self.undo_stack.push(cmd)
        self.graph_scene.select_vertices(new_verts)

    def delete_selection(self) -> None:
        selection = list(self.graph_scene.selected_vertices)
        selected_edges = list(self.graph_scene.selected_edges)
        rem_vertices = selection.copy()
        for v in selection:
            if vertex_is_w(self.graph_scene.g.type(v)):
                rem_vertices.append(get_w_partner(self.graph_scene.g, v))
        if not rem_vertices and not selected_edges: return
        new_g = copy.deepcopy(self.graph_scene.g)
        self.graph_scene.clearSelection()
        new_g.remove_edges(selected_edges)
        new_g.remove_vertices(list(set(rem_vertices)))
        cmd = SetGraph(self.graph_view,new_g) if len(set(rem_vertices)) > 128 \
            else UpdateGraph(self.graph_view,new_g)
        self.undo_stack.push(cmd)

    def add_vert(self, x: float, y: float) -> None:
        cmd = AddWNode(self.graph_view, x, y) if self._curr_vty == VertexType.W_OUTPUT \
            else AddNode(self.graph_view, x, y, self._curr_vty)
        self.undo_stack.push(cmd)

    def add_edge(self, u: VT, v: VT) -> None:
        graph = self.graph_view.graph_scene.g
        if vertex_is_w(graph.type(u)) and get_w_partner(graph, u) == v:
            return None
        if graph.type(u) == VertexType.W_INPUT and len(graph.neighbors(u)) >= 2 or \
            graph.type(v) == VertexType.W_INPUT and len(graph.neighbors(v)) >= 2:
            return None
        cmd = AddEdge(self.graph_view, u, v, self._curr_ety)
        self.undo_stack.push(cmd)

    def vert_moved(self, vs: list[tuple[VT, float, float]]) -> None:
        self.undo_stack.push(MoveNode(self.graph_view, vs))

    def vert_double_clicked(self, v: VT) -> None:
        graph = self.graph_view.graph_scene.g
        if graph.type(v) == VertexType.BOUNDARY:
            input_, ok = QInputDialog.getText(
                self, "Input Dialog", "Enter Qubit Index:"
            )
            try:
                graph.set_qubit(v, int(input_.strip()))
            except ValueError:
                show_error_msg("Wrong Input Type", "Please enter a valid input (e.g. 1, 2)")
            return None
        elif vertex_is_w(graph.type(v)):
            return None

        input_, ok = QInputDialog.getText(
            self, "Input Dialog", "Enter Desired Phase Value:"
        )
        if not ok:
            return None
        try:
            new_phase = string_to_phase(input_)
        except ValueError:
            show_error_msg("Wrong Input Type", "Please enter a valid input (e.g. 1/2, 2)")
            return None
        cmd = ChangePhase(self.graph_view, v, new_phase)
        self.undo_stack.push(cmd)


def toolbar_select_node_edge(parent: EditorBasePanel) -> ToolbarSection:
    icon_size = QSize(32, 32)
    select = QToolButton(parent, checkable=True, checked=True)  # Selected by default
    vertex = QToolButton(parent, checkable=True)
    edge = QToolButton(parent, checkable=True)
    select.setToolTip("Select (s)")
    vertex.setToolTip("Add Vertex (v)")
    edge.setToolTip("Add Edge (e)")
    select.setIcon(QIcon(get_data("icons/tikzit-tool-select.svg")))
    vertex.setIcon(QIcon(get_data("icons/tikzit-tool-node.svg")))
    edge.setIcon(QIcon(get_data("icons/tikzit-tool-edge.svg")))
    select.setShortcut("s")
    vertex.setShortcut("v")
    edge.setShortcut("e")
    select.setIconSize(icon_size)
    vertex.setIconSize(icon_size)
    edge.setIconSize(icon_size)
    select.clicked.connect(lambda: parent._tool_clicked(ToolType.SELECT))
    vertex.clicked.connect(lambda: parent._tool_clicked(ToolType.VERTEX))
    edge.clicked.connect(lambda: parent._tool_clicked(ToolType.EDGE))
    return ToolbarSection(select, vertex, edge, exclusive=True)

def create_list_widget(parent,
                        data: dict[VertexType.Type, DrawPanelNodeType] | dict[EdgeType.Type, DrawPanelNodeType],
                        onclick: Callable[[VertexType.Type], None] | Callable[[EdgeType.Type], None]) -> QListWidget:
    list_widget = QListWidget(parent)
    list_widget.setResizeMode(QListView.ResizeMode.Adjust)
    list_widget.setViewMode(QListView.ViewMode.IconMode)
    list_widget.setMovement(QListView.Movement.Static)
    list_widget.setUniformItemSizes(True)
    list_widget.setGridSize(QSize(60, 64))
    list_widget.setWordWrap(True)
    list_widget.setIconSize(QSize(24, 24))
    for typ, value in data.items():
        icon = create_icon(*value["icon"])
        item = QListWidgetItem(icon, value["text"])
        item.setData(Qt.ItemDataRole.UserRole, typ)
        list_widget.addItem(item)
    list_widget.itemClicked.connect(lambda x: onclick(x.data(Qt.ItemDataRole.UserRole)))
    list_widget.setCurrentItem(list_widget.item(0))
    return list_widget

def create_icon(shape: ShapeType, color: str) -> QIcon:
    icon = QIcon()
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(BLACK), 6))
    painter.setBrush(QColor(color))
    if shape == ShapeType.CIRCLE:
        painter.drawEllipse(4, 4, 56, 56)
    elif shape == ShapeType.SQUARE:
        painter.drawRect(4, 4, 56, 56)
    elif shape == ShapeType.TRIANGLE:
        painter.drawPolygon([QPoint(32, 10), QPoint(2, 60), QPoint(62, 60)])
    elif shape == ShapeType.LINE:
        painter.drawLine(0, 32, 64, 32)
    elif shape == ShapeType.DASHED_LINE:
        painter.setPen(QPen(QColor(color), 6, Qt.PenStyle.DashLine))
        painter.drawLine(0, 32, 64, 32)
    painter.end()
    icon.addPixmap(pixmap)
    return icon

def string_to_phase(string: str) -> Fraction:
    if not string:
        return Fraction(0)
    try:
        s = string.lower().replace(' ', '')
        s = s.replace('\u03c0', '').replace('pi', '')
        if '.' in s or 'e' in s:
            return Fraction(float(s))
        elif '/' in s:
            a, b = s.split("/", 2)
            if not a:
                return Fraction(1, int(b))
            if a == '-':
                a = '-1'
            return Fraction(int(a), int(b))
        else:
            return Fraction(int(s))
    except ValueError:
        return sympify(string)
