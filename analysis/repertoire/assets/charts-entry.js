import * as echarts from 'echarts/core';
import {BarChart,HeatmapChart} from 'echarts/charts';
import {GridComponent,LegendComponent,TooltipComponent,DataZoomComponent,VisualMapComponent,TitleComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
echarts.use([BarChart,HeatmapChart,GridComponent,LegendComponent,TooltipComponent,DataZoomComponent,VisualMapComponent,TitleComponent,AriaComponent,CanvasRenderer]);
window.ReportECharts=echarts;
