import threading
from imgui_bundle import imgui, icons_fontawesome_6 as ifa6
import typing
import natsort

import glassesTools
from glassesTools.gui import utils as gt_gui_utils

from ... import process, session


class ColumnSpec(typing.NamedTuple):
    name: str
    flags: int
    display_func:  typing.Callable[[session.Session],None]
    sort_key_func: typing.Callable[[int|str],typing.Any]
    header_lbl: str|None=None   # if set, different string than name is used for the column header. Works only for non-angled headers
    before_recordings: bool=False

class List:
    def __init__(self,
                 items: dict[int|str, session.Session],
            items_lock: threading.Lock,
        selected_items: dict[int|str, bool],
        info_callback: typing.Callable = None,
        draw_action_status_callback: typing.Callable = None,
        item_context_callback: typing.Callable = None):

        self.items = items
        self.selected_items = selected_items
        self.items_lock     = items_lock

        self.info_callback  = info_callback
        self.draw_action_status_callback = draw_action_status_callback
        self.item_context_callback  = item_context_callback

        self.sorted_ids: list[int|str] = []
        self._last_clicked_id: int|str = None
        self._require_sort: bool = True

        self.display_actions: list[process.Action] = []
        self._view_column_count = None
        self._column_layout_signature: tuple[tuple[str, str|process.Action|None], ...]|None = None
        self._column_layout_changed = False

        self._extra_columns: list[ColumnSpec] = None

        self._last_y = None

        self.table_flags: int = (
            imgui.TableFlags_.scroll_x |
            imgui.TableFlags_.scroll_y |
            imgui.TableFlags_.hideable |
            imgui.TableFlags_.sortable |
            imgui.TableFlags_.sort_multi |
            imgui.TableFlags_.reorderable |
            imgui.TableFlags_.row_bg |
            imgui.TableFlags_.sizing_fixed_fit |
            imgui.TableFlags_.highlight_hovered_column |
            imgui.TableFlags_.borders_inner_v |
            imgui.TableFlags_.no_borders_in_body_until_resize
        )

    def _column_layout(self) -> list[tuple[str, ColumnSpec|process.Action|None]]:
        columns: list[tuple[str, ColumnSpec|process.Action|None]] = [('selector', None), ('name', None)]
        extras = self._extra_columns or []
        columns.extend(('extra', column) for column in extras if column.before_recordings)
        columns.append(('recordings', None))
        columns.extend(('action', action) for action in self.display_actions)
        columns.extend(('extra', column) for column in extras if not column.before_recordings)
        return columns

    def set_actions_to_show(self, actions: set[process.Action]):
        self.display_actions = [k for k in process.Action if k in actions] # filter out crap and fix display order
        self._determine_column_count()

    def set_extra_columns(self, columns: list[ColumnSpec]|None):
        self._extra_columns = columns
        self._determine_column_count()

    def request_sort(self):
        self._require_sort = True

    def _determine_column_count(self):
        column_layout = self._column_layout()
        signature = tuple((kind, value.name if isinstance(value, ColumnSpec) else value) for kind, value in column_layout)
        if self._column_layout_signature is not None and signature != self._column_layout_signature:
            self._column_layout_changed = True
        self._column_layout_signature = signature
        self._view_column_count = len(column_layout)

    def draw(self, limit_outer_size=False):
        with self.items_lock:
            num_items = len(self.items)
        if num_items==0:
            imgui.text_wrapped('There are no sessions')
            return
        outer_size = imgui.ImVec2(0,0)
        if limit_outer_size and self._last_y is not None:
            outer_size.y = self._last_y+imgui.get_style().item_spacing.y
        if imgui.begin_table(
            f"##session_list",
            columns=self._view_column_count,
            flags=self.table_flags,
            outer_size=outer_size
        ):
            frame_height = imgui.get_frame_height()
            column_layout = self._column_layout()

            # Setup
            checkbox_width = frame_height-2*imgui.get_style().frame_padding.y
            for kind, value in column_layout:
                if kind == 'selector':
                    imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_reorder | imgui.TableColumnFlags_.no_header_label, init_width_or_weight=checkbox_width)
                elif kind == 'name':
                    imgui.table_setup_column("Name", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)
                elif kind == 'recordings':
                    imgui.table_setup_column("Recordings", imgui.TableColumnFlags_.angled_header)
                elif kind == 'action':
                    action = typing.cast(process.Action, value)
                    imgui.table_setup_column(action.displayable_name, imgui.TableColumnFlags_.angled_header)
                else:
                    column = typing.cast(ColumnSpec, value)
                    imgui.table_setup_column(column.name, column.flags)

            if self._column_layout_changed and gt_gui_utils.reset_table_display_order(len(column_layout)):
                self._column_layout_changed = False

            # Sticky column headers and selector row
            if imgui.table_get_column_flags(0) & imgui.TableColumnFlags_.is_enabled:
                imgui.table_setup_scroll_freeze(1, 2)  # Sticky column headers and selector row
            else:
                imgui.table_setup_scroll_freeze(0, 2)  # Sticky column headers

            # Sorting
            with self.items_lock:
                sort_specs = imgui.table_get_sort_specs()
                if set(self.sorted_ids) != set(self.selected_items.keys()):
                    self._require_sort = True
                self._sort_items(sort_specs)

                if (imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows | imgui.FocusedFlags_.dock_hierarchy) and not imgui.get_io().want_text_input and imgui.get_io().key_ctrl and imgui.is_key_pressed(imgui.Key.a, repeat=False)):
                    # ctrl+A pressed, select all visible sessions
                    glassesTools.utils.set_all(self.selected_items, True, subset = self.sorted_ids)

                # Headers
                imgui.table_angled_headers_row()
                imgui.table_next_row(imgui.TableRowFlags_.headers)
                for c_idx, (kind, value) in enumerate(column_layout):
                    if not imgui.table_set_column_index(c_idx):
                        continue
                    if c_idx==0:  # checkbox column: reflects whether all, some or none of visible sessions are selected, and allows selecting all or none
                        # get state
                        num_selected = sum([self.selected_items[iid] for iid in self.sorted_ids])
                        if num_selected==0:
                            # none selected
                            multi_selected_state = -1
                        elif num_selected==len(self.sorted_ids):
                            # all selected
                            multi_selected_state = 1
                        else:
                            # some selected
                            multi_selected_state = 0

                        if multi_selected_state==0:
                            imgui.push_item_flag(imgui.ItemFlags_.mixed_value, True)
                        clicked, new_state = glassesTools.gui.utils.my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,0), do_vertical_align=False)
                        if multi_selected_state==0:
                            imgui.pop_item_flag()

                        if clicked:
                            glassesTools.utils.set_all(self.selected_items, new_state, subset = self.sorted_ids)
                    else:
                        if kind == 'name':
                            column_name = 'Name'
                        elif kind == 'recordings':
                            column_name = 'Recordings'
                        elif kind == 'action':
                            column_name = typing.cast(process.Action, value).displayable_name
                        else:
                            column = typing.cast(ColumnSpec, value)
                            column_name = column.header_lbl if column.header_lbl is not None else column.name
                        if imgui.table_get_column_flags(c_idx) & imgui.TableColumnFlags_.no_header_label:
                            column_name = '##'+column_name
                        imgui.table_header(column_name)

                # Loop rows
                any_selectable_clicked = False
                if self.sorted_ids and self._last_clicked_id not in self.sorted_ids:
                    # default to topmost if last_clicked unknown, or no longer on screen due to filter
                    self._last_clicked_id = self.sorted_ids[0]
                for iid in self.sorted_ids:
                    imgui.table_next_row()

                    item = self.items[iid]
                    num_columns_drawn = 0
                    selectable_clicked = False
                    checkbox_clicked, checkbox_hovered = False, False
                    config_button_hovered = False
                    has_drawn_hitbox = False
                    for ri in range(self._view_column_count+1):
                        if not (imgui.table_get_column_flags(ri) & imgui.TableColumnFlags_.is_enabled):
                            continue
                        imgui.table_set_column_index(ri)

                        # Row hitbox
                        if not has_drawn_hitbox:
                            # hitbox needs to be drawn before anything else on the row so that, together with imgui.set_item_allow_overlap(), hovering button
                            # or checkbox on the row will still be correctly detected.
                            # this is super finicky, but works. The below together with using a height of frame_height+cell_padding_y
                            # makes the table row only cell_padding_y/2 longer. The whole row is highlighted correctly
                            cell_padding_y = imgui.get_style().cell_padding.y
                            cur_pos_y = imgui.get_cursor_pos_y()
                            imgui.set_cursor_pos_y(cur_pos_y - cell_padding_y/2)
                            imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                            imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,0.))
                            imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,cell_padding_y))
                            selectable_clicked, selectable_out = imgui.selectable(f"##{iid}_hitbox", self.selected_items[iid], flags=imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap|imgui.internal.SelectableFlagsPrivate_.select_on_click, size=(0,frame_height+cell_padding_y))
                            imgui.set_cursor_pos_y(cur_pos_y)   # instead of imgui.same_line(), we just need this part of its effect
                            imgui.pop_style_var(3)
                            selectable_right_clicked, selectables_edited = glassesTools.gui.utils.handle_item_hitbox_events(iid, self.selected_items, context_menu=self.item_context_callback)
                            self._require_sort |= selectables_edited
                            has_drawn_hitbox = True

                        if num_columns_drawn==1:
                            # (Invisible) button because it aligns the following draw calls to center vertically
                            imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                            imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,imgui.get_style().frame_padding.y))
                            imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,imgui.get_style().item_spacing.y))
                            imgui.push_style_color(imgui.Col_.button, (0.,0.,0.,0.))
                            imgui.button(f"##{item.name}_id", size=(imgui.FLT_MIN, 0))
                            imgui.pop_style_color()
                            imgui.pop_style_var(3)

                            imgui.same_line()

                        kind, value = column_layout[ri]
                        if kind == 'selector':
                            checkbox_clicked, checkbox_out = glassesTools.gui.utils.my_checkbox(f"##{iid}_selected", self.selected_items[iid], frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,imgui.get_style().frame_padding.y))
                            checkbox_hovered = imgui.is_item_hovered()
                        elif kind == 'name':
                            if self.info_callback:
                                if imgui.button(f"{ifa6.ICON_FA_CIRCLE_INFO}##{iid}_info"):
                                    self._show_item_info(iid)
                                glassesTools.gui.utils.draw_hover_text('More info about this session, and access session-specific configuration', '')
                                config_button_hovered = imgui.is_item_hovered()
                                imgui.same_line()
                            imgui.text(item.name)
                        elif kind == 'recordings':
                            n_rec = len(item.definition.recordings)
                            missing_recs = item.missing_recordings()
                            clr = glassesTools.gui.colors.error if missing_recs else glassesTools.gui.colors.ok
                            imgui.text_colored(clr, f'{n_rec-len(missing_recs)}/{n_rec}')
                        elif kind == 'action':
                            self.draw_action_status_callback(item, typing.cast(process.Action, value))
                        else:
                            typing.cast(ColumnSpec, value).display_func(item)
                        num_columns_drawn+=1

                    # handle selection logic
                    # NB: the part of this logic that has to do with right-clicks is in handle_item_hitbox_events()
                    # NB: any_selectable_clicked is just for handling clicks not on any item
                    any_selectable_clicked = any_selectable_clicked or selectable_clicked or selectable_right_clicked

                    self._last_clicked_id = glassesTools.gui.utils.selectable_item_logic(
                        iid, self.selected_items, self._last_clicked_id, self.sorted_ids,
                        selectable_clicked, selectable_out, overlayed_hovered=checkbox_hovered or config_button_hovered,
                        overlayed_clicked=checkbox_clicked, new_overlayed_state=checkbox_out
                        )

                    # further deal with doubleclick on item
                    if selectable_clicked and not checkbox_hovered: # don't enter this branch if interaction is with checkbox on the table row
                        if not imgui.get_io().key_ctrl and not imgui.get_io().key_shift and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                            self._show_item_info(iid)

                self._last_y = imgui.get_cursor_pos().y
                last_cursor_y = imgui.get_cursor_screen_pos().y
                imgui.end_table()

                # handle click in table area outside header+contents:
                # deselect all, and if right click, show popup
                # check mouse is below bottom of last drawn row so that clicking on the one pixel empty space between selectables
                # does not cause everything to unselect or popup to open
                if imgui.is_item_clicked(imgui.MouseButton_.left) and not any_selectable_clicked and imgui.get_io().mouse_pos.y>last_cursor_y:  # NB: table header is not signalled by is_item_clicked(), so this works correctly
                    glassesTools.utils.set_all(self.selected_items, False)

            # show menu when right-clicking the empty space
            # TODO

    def _show_item_info(self, iid):
        if self.info_callback:
            self.info_callback(self.items[iid])

    def _sort_items(self, sort_specs_in: imgui.TableSortSpecs):
        if sort_specs_in.specs_dirty or self._require_sort:
            ids = list(self.items)
            sort_specs = [sort_specs_in.get_specs(i) for i in range(sort_specs_in.specs_count)]
            for sort_spec in reversed(sort_specs):
                key: typing.Callable[[int|str],typing.Any]
                kind, value = self._column_layout()[sort_spec.column_index]
                if kind == 'name':
                    key = natsort.os_sort_keygen(key=lambda iid: self.items[iid].name)
                elif kind == 'recordings':
                    key = lambda iid: self.items[iid].num_present_recordings()
                elif kind == 'action':
                    action = typing.cast(process.Action, value)
                    if process.is_session_level_action(action):
                        key = lambda iid: 999 if not self.items[iid].has_all_recordings() else self.items[iid].state[action]
                    else:
                        key = lambda iid: 999 if not self.items[iid].has_all_recordings() else self.items[iid].action_completed_num_recordings(action)
                elif kind == 'extra':
                    key = typing.cast(ColumnSpec, value).sort_key_func
                else:
                    continue
                ids.sort(key=key, reverse=sort_spec.get_sort_direction()==imgui.SortDirection.descending)
            self.sorted_ids = ids
            sort_specs_in.specs_dirty = False
            self._require_sort = False