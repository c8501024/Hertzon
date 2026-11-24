"""The VTK classes the modeler view and the field plots use, from the VTK modules that hold them.

``import vtk`` loads every VTK module, about 370 libraries (580 MB) of which Hertzon needs about 110 (pyvista
loads those itself). On the first start after an installation or update every new library is checked by the
virus scanner (Windows) or the system's code check (macOS) before it loads, and the 260 unused ones made that
first start minutes longer (issue #474)."""
from vtkmodules.vtkCommonCore import vtkPoints, vtkUnsignedCharArray  # noqa: F401
from vtkmodules.vtkCommonDataModel import (VTK_QUADRATIC_TETRA, VTK_TETRA, vtkCellArray,  # noqa: F401
                                           vtkNonMergingPointLocator, vtkPlane, vtkPolyData)
from vtkmodules.vtkCommonMath import vtkMatrix4x4  # noqa: F401
from vtkmodules.vtkFiltersCore import vtkCutter  # noqa: F401
from vtkmodules.vtkFiltersModeling import vtkPolyDataPointSampler  # noqa: F401
from vtkmodules.vtkFiltersSources import vtkGlyphSource2D  # noqa: F401
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera  # noqa: F401
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor  # noqa: F401
from vtkmodules.vtkRenderingCore import (vtkActor, vtkActor2D, vtkCellPicker, vtkDataSetMapper,  # noqa: F401
                                         vtkPointPicker, vtkPolyDataMapper2D, vtkRenderer, vtkTextActor)
