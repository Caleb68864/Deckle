"""Desktop editor for card faces and explicit front/back pairings."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace

from deckle.core.card_import import import_card_folder, import_card_images
from deckle.core.card_pairing import apply_pairing, suggest_card_pairs
from deckle.core.cards import (
    append_fronts, assign_back, materialize_shared_back, remove_cards,
    reorder_cards, set_back_mode, set_card_copies, set_card_skipped,
    set_face_rotation, set_shared_back,
)
from deckle.core.models import CardFace, CardDeck


class CardsView:
    def __init__(self, state, parent=None, on_change=None):
        from PySide6.QtCore import Qt, Signal
        from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel,
            QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
            QVBoxLayout, QWidget, QInputDialog)
        self.state, self.on_change = state, on_change or (lambda: None)
        self.on_advisory = None
        self.on_selected = None
        self.widget = QWidget(parent)
        self._thumbnail_signature = None
        self._thumbnail_worker = None
        self._thumbnail_icons = OrderedDict()
        self._thumbnail_refs = {}
        layout = QVBoxLayout(self.widget)
        buttons=QVBoxLayout()
        self.import_fronts_button=QPushButton("Import front folder…")
        self.import_back_button=QPushButton("Import back image(s)…")
        self.import_back_folder_button=QPushButton("Import back folder…")
        buttons.addWidget(self.import_fronts_button)
        back_imports=QHBoxLayout()
        back_imports.addWidget(self.import_back_button); back_imports.addWidget(self.import_back_folder_button)
        buttons.addLayout(back_imports)
        layout.addLayout(buttons)
        row=QHBoxLayout(); row.addWidget(QLabel("Back mode:"))
        self.mode=QComboBox(); self.mode.addItem("Individual backs","individual"); self.mode.addItem("One shared back","shared")
        row.addWidget(self.mode); self.shared_label=QLabel(""); row.addWidget(self.shared_label); row.addStretch(1); layout.addLayout(row)
        actions=QVBoxLayout()
        self.match_name_button=QPushButton("Match by filename")
        self.match_order_button=QPushButton("Match by order")
        self.remove_button=QPushButton("Remove selected")
        self.up_button=QPushButton("Move up"); self.down_button=QPushButton("Move down")
        self.rotate_front_button=QPushButton("Rotate front 90°")
        self.rotate_back_button=QPushButton("Rotate back 90°")
        pairing_actions=QHBoxLayout()
        pairing_actions.addWidget(self.match_name_button); pairing_actions.addWidget(self.match_order_button)
        row_actions=QHBoxLayout()
        row_actions.addWidget(self.up_button); row_actions.addWidget(self.down_button); row_actions.addWidget(self.remove_button)
        rotation_actions=QHBoxLayout()
        rotation_actions.addWidget(self.rotate_front_button); rotation_actions.addWidget(self.rotate_back_button)
        actions.addLayout(pairing_actions); actions.addLayout(row_actions); actions.addLayout(rotation_actions)
        layout.addLayout(actions)
        class ReorderableCardTable(QTableWidget):
            reorder_requested=Signal(list,int)
            def dropEvent(table,event):
                rows=sorted({index.row() for index in table.selectionModel().selectedRows()})
                hit=table.indexAt(event.position().toPoint())
                target=hit.row() if hit.isValid() else table.rowCount()
                if hit.isValid() and table.dropIndicatorPosition()==QAbstractItemView.DropIndicatorPosition.BelowItem: target+=1
                event.setDropAction(Qt.DropAction.IgnoreAction); event.accept()
                if rows:table.reorder_requested.emit(rows,target)
        self.table=ReorderableCardTable(0,5,self.widget); self.table.setHorizontalHeaderLabels(["#","Front","Back","Copies","Skip"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.table.setDragEnabled(True); self.table.setAcceptDrops(True); self.table.setDropIndicatorShown(True)
        self.table.setIconSize(__import__("PySide6.QtCore",fromlist=["QSize"]).QSize(48,64)); self.table.verticalHeader().setDefaultSectionSize(72)
        layout.addWidget(self.table)
        self.count_label=QLabel(""); layout.addWidget(self.count_label)
        self.import_fronts_button.clicked.connect(self.import_fronts)
        self.import_back_button.clicked.connect(self.import_backs)
        self.import_back_folder_button.clicked.connect(self.import_backs_from_folder)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.match_name_button.clicked.connect(lambda: self.match("filename"))
        self.match_order_button.clicked.connect(lambda: self.match("order"))
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self.move(-1)); self.down_button.clicked.connect(lambda: self.move(1))
        self.rotate_front_button.clicked.connect(lambda: self.rotate_selected("front"))
        self.rotate_back_button.clicked.connect(lambda: self.rotate_selected("back"))
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._edit_face)
        self.table.reorder_requested.connect(self._reorder_rows)
        self.table.verticalScrollBar().valueChanged.connect(lambda *_: self._request_thumbnails())
        self.table.currentCellChanged.connect(lambda *_:(self._request_thumbnails(),self._show_selected_faces()))
        self.refresh()

    @property
    def deck(self): return self.state.project.card_deck

    def _commit_deck(self, deck):
        self.state.mutate(lambda project: replace(project, card_deck=deck))
        self.refresh(); self.on_change()

    def _import_done(self, result, fronts, replace_fronts=False):
        deck=self.deck
        assets=deck.assets+result.assets
        updated=replace(deck,assets=assets)
        if fronts and replace_fronts: updated=replace(updated,cards=())
        if fronts:
            updated=append_fronts(updated,[a.id for a in result.assets])
        elif deck.back_mode=="shared":
            if result.assets: updated=set_shared_back(updated,CardFace(result.assets[0].id))
        self._commit_deck(updated)
        if result.warnings and self.on_advisory:
            self.on_advisory(" ".join(w.detail for w in result.warnings))

    def _start_import(self, function, args, fronts, replace_fronts=False):
        from PySide6.QtCore import QThread, Signal
        if getattr(self,"_worker",None) is not None and self._worker.isRunning():return
        class Worker(QThread):
            result=Signal(object); failure=Signal(str)
            def run(worker):
                try: worker.result.emit(function(*args))
                except Exception as exc: worker.failure.emit(str(exc))
        worker=Worker(self.widget); self._worker=worker
        state=self.state
        original_cards=self.deck.cards
        self._worker=worker
        for button in (self.import_fronts_button,self.import_back_button,self.import_back_folder_button):button.setEnabled(False)
        def finish():
            for button in (self.import_fronts_button,self.import_back_button,self.import_back_folder_button):button.setEnabled(True)
            if self._worker is worker:self._worker=None
        def completed(result):
            if self.state is state:
                if replace_fronts and self.deck.cards!=original_cards:self._show_error("The card list changed during import. No fronts were replaced; start the replacement again to review the current deck.")
                else:self._import_done(result,fronts,replace_fronts)
            finish()
        def failed(message):
            if self.state is state:self._show_error(message)
            finish()
        worker.result.connect(completed)
        worker.failure.connect(failed)
        worker.start()

    def import_fronts(self):
        from PySide6.QtWidgets import QFileDialog
        path=QFileDialog.getExistingDirectory(self.widget,"Import front images")
        if path:
            replace_fronts=False
            if self.deck.cards:
                from PySide6.QtWidgets import QMessageBox
                box=QMessageBox(self.widget); box.setWindowTitle("Add or replace fronts?")
                box.setText(f"Add the new images after {len(self.deck.cards)} existing card(s), or replace all current fronts?")
                append=box.addButton("Add fronts",QMessageBox.ButtonRole.AcceptRole)
                replace_button=box.addButton("Replace all fronts",QMessageBox.ButtonRole.DestructiveRole)
                box.addButton(QMessageBox.StandardButton.Cancel); box.setDefaultButton(append); box.exec()
                if box.clickedButton() is replace_button: replace_fronts=True
                elif box.clickedButton() is not append:return
            self._start_import(import_card_folder,(path,),True,replace_fronts)

    def import_backs(self):
        from PySide6.QtWidgets import QFileDialog
        if self.deck.back_mode=="shared":
            path,_=QFileDialog.getOpenFileName(self.widget,"Choose shared back image","","Image files (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp)")
            paths=[path] if path else []
        else:
            paths,_=QFileDialog.getOpenFileNames(self.widget,"Import individual back images","","Image files (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp)")
        if paths: self._start_import(import_card_images,(paths,),False)

    def import_backs_from_folder(self):
        from PySide6.QtWidgets import QFileDialog
        path=QFileDialog.getExistingDirectory(self.widget,"Import individual back images")
        if path:self._start_import(import_card_folder,(path,),False)

    def _show_error(self, text):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self.widget,"Card image import failed",text)

    def change_mode(self,index):
        mode=self.mode.currentData()
        if mode==self.deck.back_mode: return
        if mode=="individual" and self.deck.shared_back is not None:
            from PySide6.QtWidgets import QMessageBox
            answer=QMessageBox.question(self.widget,"Use shared back for every card?","Copy the shared back to each card before switching to individual backs?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:
                self.refresh(); return
            deck=materialize_shared_back(self.deck) if answer==QMessageBox.StandardButton.Yes else set_back_mode(self.deck,mode)
        else: deck=set_back_mode(self.deck,mode)
        self._commit_deck(deck)

    def match(self,method):
        from PySide6.QtWidgets import QMessageBox
        fronts=[a for a in self.deck.assets if any(c.front.asset_id==a.id for c in self.deck.cards)]
        backs=[a for a in self.deck.assets if all(c.front.asset_id!=a.id for c in self.deck.cards)]
        proposal=suggest_card_pairs(fronts,backs,method)
        labels={asset.id:asset.label for asset in self.deck.assets}
        matched_fronts={front for front,_ in proposal.matches}
        replacements=sum(1 for card in self.deck.cards if card.front.asset_id in matched_fronts and card.back is not None)
        details=[f"{labels.get(front,front)}  →  {labels.get(back,back)}" for front,back in proposal.matches]
        details += [f"Unmatched front: {labels.get(asset_id,asset_id)}" for asset_id in proposal.unmatched_front_ids]
        details += [f"Unused back: {labels.get(asset_id,asset_id)}" for asset_id in proposal.unused_back_ids]
        details += [f"Ambiguous filename key: {key}" for key in proposal.ambiguous_keys]
        box=QMessageBox(self.widget); box.setWindowTitle("Review suggested pairs")
        box.setText(f"{len(proposal.matches)} proposed pair(s); {len(proposal.unmatched_front_ids)} unmatched front(s); {len(proposal.unused_back_ids)} unused back(s); {len(proposal.ambiguous_keys)} ambiguous name(s).")
        box.setInformativeText("Existing manual backs are preserved unless you explicitly replace them.")
        box.setDetailedText("\n".join(details) or "No filename/order matches were found.")
        apply_button=box.addButton("Apply to missing backs",QMessageBox.ButtonRole.AcceptRole)
        replace_button=box.addButton(f"Replace {replacements} existing pair(s)",QMessageBox.ButtonRole.DestructiveRole) if replacements else None
        box.addButton(QMessageBox.StandardButton.Cancel); box.setDefaultButton(apply_button); box.exec()
        if box.clickedButton() is apply_button:self._commit_deck(apply_pairing(self.deck,proposal))
        elif replace_button is not None and box.clickedButton() is replace_button:self._commit_deck(apply_pairing(self.deck,proposal,replace_existing=True))

    def _selected_card_ids(self):
        rows=sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.table.item(row,0).data(256) for row in rows]

    def remove_selected(self):
        ids=self._selected_card_ids()
        if ids: self._commit_deck(remove_cards(self.deck,ids))

    def move(self,direction):
        ids=[c.id for c in self.deck.cards]; selected=self._selected_card_ids()
        if len(selected)!=1:return
        index=ids.index(selected[0]); target=index+direction
        if not 0<=target<len(ids):return
        ids[index],ids[target]=ids[target],ids[index]
        self._commit_deck(reorder_cards(self.deck,ids)); self.table.selectRow(target)

    def _reorder_rows(self,rows,target):
        ordered=[card.id for card in self.deck.cards]
        moving=[ordered[row] for row in rows]
        moving_set=set(moving)
        remaining=[card_id for card_id in ordered if card_id not in moving_set]
        at=max(0,min(len(remaining),target-sum(row<target for row in rows)))
        result=remaining[:at]+moving+remaining[at:]
        if result!=ordered:
            self._commit_deck(reorder_cards(self.deck,result))
            for row in range(at,at+len(moving)):self.table.selectRow(row)

    def rotate_selected(self,side):
        selected=self._selected_card_ids()
        if not selected:return
        if side=="back" and self.deck.back_mode=="shared":
            face=self.deck.shared_back
            if face is not None:
                self._commit_deck(set_face_rotation(self.deck,selected[0],side,(face.rotate_deg+90)%360))
            return
        cards=[]; changed=False
        for card in self.deck.cards:
            if card.id not in selected: cards.append(card); continue
            face=card.front if side=="front" else card.back
            if face is None: cards.append(card); continue
            cards.append(replace(card,**{side:replace(face,rotate_deg=(face.rotate_deg+90)%360)})); changed=True
        if changed:self._commit_deck(replace(self.deck,cards=tuple(cards)))

    def _item_changed(self,item):
        if item.column()!=4:return
        card_id=item.data(256)
        from PySide6.QtCore import Qt
        self._commit_deck(set_card_skipped(self.deck,card_id,item.checkState()==Qt.CheckState.Checked))

    def _edit_face(self,row,column):
        from PySide6.QtWidgets import QInputDialog
        card=self.deck.cards[row]
        if column==2 and self.deck.back_mode=="shared": return
        if column==3:
            value,ok=QInputDialog.getInt(self.widget,"Copies","Copies of this card:",card.copies,1,999)
            if ok:self._commit_deck(set_card_copies(self.deck,card.id,value))
        elif column in (1,2):
            pool=self.deck.assets
            if column==1: old=card.front.asset_id
            else: old=card.back.asset_id if card.back else ""
            labels=[f"{a.label} — {a.original_path}" for a in pool]; initial=next((i for i,a in enumerate(pool) if a.id==old),0)
            label,ok=QInputDialog.getItem(self.widget,"Choose card face","Search/select image:",labels,initial,True)
            if ok and labels:
                asset=next((a for a,text in zip(pool,labels) if text==label),None)
                if asset is None:return
                if column==1:
                    updated=replace(self.deck,cards=tuple(replace(c,front=CardFace(asset.id)) if c.id==card.id else c for c in self.deck.cards))
                    self._commit_deck(updated)
                else:self._commit_deck(assign_back(self.deck,card.id,CardFace(asset.id)))

    def refresh(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem, QSpinBox
        deck=self.deck
        if deck is None:
            self.table.setRowCount(0); self.shared_label.setText(""); self.count_label.setText(""); return
        self.mode.blockSignals(True); self.mode.setCurrentIndex(0 if deck.back_mode=="individual" else 1); self.mode.blockSignals(False)
        labels={a.id:a.label for a in deck.assets}
        self.shared_label.setText("Shared: "+labels.get(deck.shared_back.asset_id,"No shared back") if deck.shared_back else "No shared back selected")
        self.table.blockSignals(True); self.table.setRowCount(len(deck.cards))
        for row,card in enumerate(deck.cards):
            for col,value in ((0,str(row+1)),(1,labels.get(card.front.asset_id,"Missing image")),(2,"Shared: "+labels.get(deck.shared_back.asset_id,"Missing") if deck.back_mode=="shared" and deck.shared_back else labels.get(card.back.asset_id,"Missing back") if card.back else "Missing back")):
                item=QTableWidgetItem(value); item.setData(256,card.id); self.table.setItem(row,col,item)
                face=card.front if col==1 else (deck.shared_back if deck.back_mode=="shared" else card.back) if col==2 else None
                if face is not None and face.asset_id in self._thumbnail_icons:item.setIcon(self._thumbnail_icons[face.asset_id])
            spin=QSpinBox(); spin.setRange(1,999); spin.setValue(card.copies); spin.valueChanged.connect(lambda value,cid=card.id:self._commit_deck(set_card_copies(self.deck,cid,value)))
            self.table.setCellWidget(row,3,spin)
            skipped=QTableWidgetItem(""); skipped.setFlags(skipped.flags()|Qt.ItemFlag.ItemIsUserCheckable); skipped.setCheckState(Qt.CheckState.Checked if card.skipped else Qt.CheckState.Unchecked); skipped.setData(256,card.id); self.table.setItem(row,4,skipped)
        self.table.blockSignals(False)
        active=sum(card.copies for card in deck.cards if not card.skipped)
        self.count_label.setText(f"{len(deck.cards)} entr{'y' if len(deck.cards)==1 else 'ies'} · {active} active card{'s' if active!=1 else ''}")
        self.rotate_back_button.setEnabled(deck.back_mode!="shared" or deck.shared_back is not None)
        self.match_name_button.setEnabled(deck.back_mode=="individual")
        self.match_order_button.setEnabled(deck.back_mode=="individual")
        self._request_thumbnails()

    def _request_thumbnails(self):
        from PySide6.QtCore import QThread, Signal
        deck=self.deck
        if deck is None or not deck.cards:return
        first=self.table.rowAt(0); last=self.table.rowAt(max(0,self.table.viewport().height()-1))
        if first<0:first=0
        if last<0:last=min(len(deck.cards)-1,first+19)
        row_ids=set(range(first,min(len(deck.cards),last+1)))
        current=self.table.currentRow()
        if current>=0:row_ids.add(current)
        asset_ids=[]
        for row in sorted(row_ids):
            card=deck.cards[row]
            asset_ids.append(card.front.asset_id)
            back=deck.shared_back if deck.back_mode=="shared" else card.back
            if back is not None:asset_ids.append(back.asset_id)
        by_id={asset.id:asset for asset in deck.assets}
        assets=tuple(by_id[asset_id] for asset_id in dict.fromkeys(asset_ids) if asset_id in by_id and self._thumbnail_refs.get(asset_id)!=by_id[asset_id].page.ref)
        if not assets:return
        signature=tuple((asset.id,asset.page.ref) for asset in assets)
        if signature==self._thumbnail_signature:return
        self._thumbnail_signature=signature
        class Worker(QThread):
            ready=Signal(object); failed=Signal(str)
            def run(worker):
                try:
                    from deckle.core.render import thumbnails
                    worker.ready.emit((assets,thumbnails([asset.page for asset in assets],0,len(assets),dpi=36)))
                except Exception as exc:worker.failed.emit(str(exc))
        worker=Worker(self.widget); self._thumbnail_worker=worker
        worker.ready.connect(lambda result:self._thumbnails_ready(worker,signature,*result))
        worker.failed.connect(lambda message:self._thumbnails_failed(worker,signature,message))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _thumbnails_ready(self,worker,signature,assets,rendered):
        if worker is not self._thumbnail_worker:return
        from PySide6.QtGui import QImage,QPixmap,QIcon
        current={asset.id:asset.page.ref for asset in self.deck.assets} if self.deck else {}
        for asset,page in zip(assets,rendered):
            # A project can change while pdfium is rendering. Ignore assets
            # whose ID was reused for a different source in the meantime.
            if current.get(asset.id)!=asset.page.ref:continue
            if not page.width or not page.height:continue
            image=QImage(page.rgba,page.width,page.height,page.width*4,QImage.Format.Format_RGBA8888).copy()
            self._thumbnail_icons[asset.id]=QIcon(QPixmap.fromImage(image))
            self._thumbnail_icons.move_to_end(asset.id)
            self._thumbnail_refs[asset.id]=asset.page.ref
        while len(self._thumbnail_icons)>80:
            expired,_=self._thumbnail_icons.popitem(last=False)
            self._thumbnail_refs.pop(expired,None)
        self.refresh()

    def _thumbnails_failed(self,worker,signature,message):
        if worker is not self._thumbnail_worker:return
        current={asset.id:asset.page.ref for asset in self.deck.assets} if self.deck else {}
        if any(current.get(asset_id)!=ref for asset_id,ref in signature):return
        self._thumbnail_signature=None
        if self.on_advisory:self.on_advisory(f"Could not render card thumbnails: {message}")

    def _show_selected_faces(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap,QTransform
        ids=self._selected_card_ids()
        card=next((entry for entry in self.deck.cards if ids and entry.id==ids[0]),None) if self.deck else None
        if self.on_selected:self.on_selected(card.id if card else None)
        if card is None:
            self.front_preview.setPixmap(QPixmap()); self.front_preview.setText("Select a card to preview its front")
            self.back_preview.setPixmap(QPixmap()); self.back_preview.setText("Select a card to preview its back")
            return
        back=self.deck.shared_back if self.deck.back_mode=="shared" else card.back
        for label,face,caption in ((self.front_preview,card.front,"Front"),(self.back_preview,back,"Shared back" if self.deck.back_mode=="shared" else "Back")):
            pixmap=self._thumbnail_icons.get(face.asset_id).pixmap(180,240) if face and face.asset_id in self._thumbnail_icons else None
            if pixmap is None or pixmap.isNull():
                label.setPixmap(QPixmap())
                name=next((asset.label for asset in self.deck.assets if face and asset.id==face.asset_id),"Missing back")
                label.setText(f"{caption}: {name}")
            else:
                if face.rotate_deg:pixmap=pixmap.transformed(QTransform().rotate(face.rotate_deg))
                label.setText(""); label.setPixmap(pixmap.scaled(label.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
