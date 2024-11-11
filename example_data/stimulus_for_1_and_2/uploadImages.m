function [texs,szs] = uploadImages(file, wpnt)
if isfolder(file)
    [files, nf] = FileFromFolder(file,'',{'png','jpg'});
    texs = zeros(nf,1);
    szs  = zeros(nf,2);
    for t=1:nf
        [texs(t),szs(t,:)] = uploadImage(fullfile(files(t).folder,files(t).name), wpnt);
    end
else
    [texs,szs] = uploadImage(file, wpnt);
end


function [tex,sz] = uploadImage(file, wpnt)
mat = imread(file);
tex = Screen('MakeTexture', wpnt, mat);
sz  = [size(mat,2) size(mat,1)];
