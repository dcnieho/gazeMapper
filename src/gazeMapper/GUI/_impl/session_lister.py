import threading
from imgui_bundle import imgui, icons_fontawesome_6 as ifa6, imspinner
import typing

import glassesTools

from . import colors
from ... import process, session


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
        self._view_column_count_base = 3    # selector, name, recordings
        self._view_column_count = None

        self._last_y = None

        self.table_flags: int = (
            imgui.TableFlags_.scroll_x |
            imgui.TableFlags_.scroll_y |
            imgui.TableFlags_.hideable |
            imgui.TableFlags_.sortable |
            imgui.TableFlags_.sort_multi |
            imgui.TableFlags_.reorderable |
            imgui.TableFlags_.sizing_fixed_fit |
            imgui.TableFlags_.highlight_hovered_column #|
            #imgui.TableFlags_.borders_inner_v
        )

    def set_actions_to_show(self, actions: set[process.Action]):
        self.display_actions = [k for k in process.Action if k in actions] # filter out crap and fix display order
        self._view_column_count = self._view_column_count_base + len(self.display_actions)

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

            # Setup
            checkbox_width = frame_height-2*imgui.get_style().frame_padding.y
            imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_reorder | imgui.TableColumnFlags_.no_header_label, init_width_or_weight=checkbox_width)  # 0
            imgui.table_setup_column("Name", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 1
            imgui.table_setup_column("Recordings", imgui.TableColumnFlags_.angled_header)  # 2
            for k in process.Action:   # 3+
                if k in self.display_actions:
                    imgui.table_setup_column(k.displayable_name, imgui.TableColumnFlags_.angled_header)

            # Enabled columns
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

                # Headers
                imgui.table_angled_headers_row()
                imgui.table_headers_row()
                # set up checkbox column: reflects whether all, some or none of visible items are selected, and allows selecting all or none
                imgui.table_set_column_index(0)
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
                    imgui.push_item_flag(imgui.internal.ItemFlagsPrivate_.mixed_value, True)
                clicked, new_state = glassesTools.gui.utils.my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,0), do_vertical_align=False)
                if multi_selected_state==0:
                    imgui.pop_item_flag()

                if clicked:
                    glassesTools.utils.set_all(self.selected_items, new_state, subset = self.sorted_ids)

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

                        match ri:
                            case 0:
                                # Selector
                                checkbox_clicked, checkbox_out = glassesTools.gui.utils.my_checkbox(f"##{iid}_selected", self.selected_items[iid], frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,imgui.get_style().frame_padding.y))
                                checkbox_hovered = imgui.is_item_hovered()
                            case 1:
                                # Name
                                if self.info_callback:
                                    if imgui.button(f"{ifa6.ICON_FA_CIRCLE_INFO}##{iid}_info"):
                                        self._show_item_info(iid)
                                    glassesTools.gui.utils.draw_hover_text('More info about this session, and access session-specific configuration', '')
                                    config_button_hovered = imgui.is_item_hovered()
                                    imgui.same_line()
                                imgui.text(item.name)
                            case 2:
                                # Number of recordings
                                n_rec = len(item.definition.recordings)
                                missing_recs = item.missing_recordings()
                                clr = colors.error if missing_recs else colors.ok
                                imgui.text_colored(clr, f'{n_rec-len(missing_recs)}/{n_rec}{" "+ifa6.ICON_FA_TRIANGLE_EXCLAMATION if missing_recs else ""}')
                                if missing_recs:
                                    glassesTools.gui.utils.draw_hover_text('missing recordings:\n'+'\n'.join(missing_recs), '')
                            case _:
                                # task status columns
                                self.draw_action_status_callback(item, self.display_actions[ri-self._view_column_count_base])
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
                match sort_spec.column_index:
                    case 1:     # Name
                        key = lambda iid: self.items[iid].name
                    case 2:     # Number of recordings
                        key = lambda iid: self.items[iid].num_present_recordings()
                    case _:     # status indicators
                        action = self.display_actions[sort_spec.column_index-self._view_column_count_base]
                        if process.is_session_level_action(action):
                            key = lambda iid: 999 if not self.items[iid].has_all_recordings() else self.items[iid].state[action]
                        else:
                            key = lambda iid: 999 if not self.items[iid].has_all_recordings() else self.items[iid].action_completed_num_recordings(action)
                ids.sort(key=key, reverse=sort_spec.get_sort_direction()==imgui.SortDirection.descending)
            self.sorted_ids = ids
            sort_specs_in.specs_dirty = False
            self._require_sort = False

def draw_process_state(state: process.State, have_hover_popup=True):
    symbol_size = imgui.calc_text_size(ifa6.ICON_FA_CIRCLE)
    match state:
        case process.State.Not_Run:
            imgui.text_colored(colors.gray, ifa6.ICON_FA_CIRCLE)
            hover_text = 'Not run'
        case process.State.Pending:
            radius    = symbol_size.x / 2
            thickness = symbol_size.x / 3 / 2.5 # 3 is number of dots, 2.5 is nextItemKoeff in imspinner.spinner_bounce_dots()
            imspinner.spinner_bounce_dots(f'waitBounceDots', radius, thickness, color=imgui.get_style_color_vec4(imgui.Col_.text))
            hover_text = 'Pending'
        case process.State.Running:
            spinner_radii = [x/22/2*symbol_size.x for x in [22, 16, 10]]
            lw = 3.5/22/2*symbol_size.x
            imspinner.spinner_ang_triple(f'runSpinner', *spinner_radii, lw, c1=imgui.get_style_color_vec4(imgui.Col_.text), c2=colors.warning, c3=imgui.get_style_color_vec4(imgui.Col_.text))
            hover_text = 'Running'
        case process.State.Completed:
            imgui.text_colored(colors.ok, ifa6.ICON_FA_CIRCLE_CHECK)
            hover_text = 'Completed'
        case process.State.Canceled:
            imgui.text_colored(colors.warning, ifa6.ICON_FA_HAND)
            hover_text = 'Canceled'
        case process.State.Failed:
            imgui.text_colored(colors.error_bright, ifa6.ICON_FA_TRIANGLE_EXCLAMATION)
            hover_text = 'Failed'
    if have_hover_popup:
        glassesTools.gui.utils.draw_hover_text(hover_text, text='')